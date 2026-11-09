"""Verify the complete pinned voice bundle and exercise the native CLI client."""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from common import TARGET, sha256


VOICE_CHECK = 'voice-runtime'
VOICE_CHECKS = {'helper-handshake', 'runtime-initialization', 'clean-shutdown'}


def verify_voice_resources(package, expected_commit, runtime_commit=None):
    """Do not accept a matching label on a missing, changed or differently based runtime."""
    runtime_commit = runtime_commit or expected_commit
    package = Path(package).resolve(strict=True)
    voice = package / 'codex-resources/voice'
    if not voice.resolve(strict=True).is_relative_to(package):
        raise RuntimeError('The voice runtime resolves outside its package.')
    manifest = json.loads((voice / 'manifest.json').read_text())
    runtime = json.loads((voice / 'runtime.json').read_text())
    if (not isinstance(expected_commit, str)
            or re.fullmatch(r'[0-9a-f]{40}', expected_commit) is None
            or manifest.get('schemaVersion') != 1
            or manifest.get('buildCommit') != expected_commit
            or manifest.get('voiceTarget') != TARGET
            or manifest.get('nativeSourceCommit', runtime_commit) != runtime_commit
            or runtime.get('schemaVersion') != 1
            or runtime.get('sourceCommit') != runtime_commit
            or runtime.get('target') != TARGET
            or runtime.get('developmentOnly') is not False):
        raise RuntimeError('The packaged voice runtime does not match the expected helper and pinned stable native builds.')
    hashes = manifest.get('sha256', {})
    required = {'codex-resources/voice/bin/codex-voice-host',
                'codex-resources/voice/runtime.json', 'codex-resources/voice/sources.json'}
    if not isinstance(hashes, dict) or not required <= hashes.keys():
        raise RuntimeError('The voice resource inventory is incomplete.')
    count = 0
    for name, digest in hashes.items():
        relative = PurePosixPath(name)
        if name == 'bin/codex':
            # This hash belongs to the official CLI, which is replaced by our
            # separately verified custom executable. Never rewrite the inventory.
            continue
        if (relative.is_absolute() or '..' in relative.parts
                or not name.startswith('codex-resources/voice/')
                or not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            raise RuntimeError('The voice resource inventory contains an invalid entry.')
        path = (package / relative).resolve(strict=True)
        if not path.is_relative_to(voice.resolve(strict=True)) or not path.is_file():
            raise RuntimeError('A voice resource resolves outside its packaged runtime.')
        if sha256(path) != digest:
            raise RuntimeError(f'Voice resource checksum mismatch: {name}')
        count += 1
    return {'voice_build_commit': expected_commit, 'resource_count': count}


def assemble_voice_resources(package, source_commit, runtime_commit):
    """Record the newly compiled helper honestly; retain native library provenance and hashes."""
    package = Path(package)
    root = package / 'codex-resources/voice'
    helper = root / 'bin/codex-voice-host'
    actual = subprocess.run([str(helper), '--build-commit'], capture_output=True,
                            text=True, timeout=10, check=True).stdout.strip()
    if actual != source_commit:
        raise RuntimeError('The compiled voice helper has a different source identity from the CLI.')
    manifest = json.loads((root / 'manifest.json').read_text())
    metadata = json.loads((package / 'codex-package.json').read_text())
    manifest.update(buildCommit=source_commit, appVersion=metadata['version'],
                    appTarget=metadata['target'], nativeSourceCommit=runtime_commit)
    manifest['sha256']['bin/codex'] = sha256(package / 'bin/codex')
    manifest['sha256']['codex-resources/voice/bin/codex-voice-host'] = sha256(helper)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return verify_voice_resources(package, source_commit, runtime_commit)


def check_voice_runtime(package, source_commit, voice_commit):
    """Use the actual compiled identity and production helper client, without opening devices."""
    try:
        result = subprocess.run([str(Path(package) / 'bin/codex'), 'debug', 'voice'],
                                capture_output=True, text=True, timeout=45, check=True)
    except subprocess.CalledProcessError as error:
        raise RuntimeError('The native voice startup check failed; the installed package is unchanged. '
                           + (error.stderr or '')[-1200:].strip()) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('The native voice startup check timed out; the installed package is unchanged.') from error
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError('The native voice check did not return a valid report.') from error
    checks = report.get('checks') if isinstance(report, dict) else None
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or report.get('source_commit') != source_commit
            or report.get('voice_build_commit') != voice_commit
            or not isinstance(checks, list) or not all(isinstance(check, str) for check in checks)
            or not VOICE_CHECKS <= set(checks)
            or report.get('audio_streams_opened') is not False
            or report.get('network_session_started') is not False):
        raise RuntimeError('The native voice check did not prove this source and voice runtime are compatible.')
    return report
