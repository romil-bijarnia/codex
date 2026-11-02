#!/usr/bin/env node
// Keep the optional reminder mute outside the npm-owned installation.
const fs = require('node:fs');
const path = require('node:path');
const childProcess = require('node:child_process');
const { syncBuiltinESMExports } = require('node:module');
const { pathToFileURL } = require('node:url');

const upstream = '/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js';
const helper = path.join(__dirname, 'mute.py');
const disabled = path.join(__dirname, 'disabled');
const voiceFixRoot = path.join(__dirname, '..', 'voice-picker');
const permissionColorsRoot = path.join(__dirname, '..', 'permission-colors');
const args = process.argv.slice(2);
// Local terminal-only visualization command; leave the native CLI and its
// existing voice, lifecycle, and reminder integrations unchanged.
if (args[0] === 'visualize') {
  const visualize = path.join(__dirname, '..', 'visualize-cli', 'visualize.py');
  const python = fs.existsSync('/opt/homebrew/bin/python3')
    ? '/opt/homebrew/bin/python3' : '/usr/bin/python3';
  const result = childProcess.spawnSync(python, [visualize, ...args.slice(1)], {
    stdio: 'inherit',
  });
  if (result.error) console.error('Codex visualization viewer: ' + result.error.message);
  process.exit(result.status ?? 1);
}
const nonInteractive = new Set([
  'exec', 'e', 'review', 'login', 'logout', 'mcp', 'mcp-server', 'app-server',
  'exec-server', 'completion', 'sandbox', 'debug', 'apply', 'features', 'plugin',
  'update', 'queue', 'archive', 'delete', 'unarchive', 'remote-control', 'help',
  '--help', '-h', '--version', '-V',
]);
const mute = process.env.CODEX_SECURITY_SETUP_REMINDERS !== '1'
  && !fs.existsSync(disabled)
  && !args.some(arg => nonInteractive.has(arg));

if (!fs.existsSync(upstream)) {
  console.error('Codex npm installation not found. Run: npm install -g @openai/codex@latest');
  process.exit(1);
}

// Release closed, idle CLI chats immediately using Codex's built-in setting.
// Put defaults first so explicit per-invocation overrides still win.
process.argv.splice(2, 0, '-c', 'thread_unload_delay_secs=0');

const originalSpawn = childProcess.spawn;
function selectVoiceFix(file) {
  const manifestPath = path.join(voiceFixRoot, 'installation.json');
  if (process.env.CODEX_VOICE_PICKER_FIX === '0'
      || fs.existsSync(path.join(voiceFixRoot, 'disabled'))
      || !fs.existsSync(manifestPath)) return file;
  try {
    const { source } = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const stat = fs.statSync(file);
    const executable = path.join(voiceFixRoot, 'package', 'bin', 'codex');
    // A package update should use its new CLI instead of an older local build.
    if (fs.realpathSync(file) === source.path
        && stat.size === source.size
        && Math.trunc(stat.mtimeMs) === source.mtime_ms
        && fs.existsSync(executable)) return executable;
  } catch (error) {
    console.error('Codex voice-picker fix unavailable: ' + error.message);
  }
  return file;
}
function selectPermissionColorsFix(file) {
  const manifestPath = path.join(permissionColorsRoot, 'installation.json');
  if (process.env.CODEX_PERMISSION_COLORS === '0'
      || fs.existsSync(path.join(permissionColorsRoot, 'disabled'))
      || !fs.existsSync(manifestPath)) return file;
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (!manifest.active) return file;
    const stat = fs.statSync(file);
    const executable = path.join(permissionColorsRoot, 'package', 'bin', 'codex');
    // Retain any earlier selected integration and fall back safely after updates.
    if (fs.realpathSync(file) === manifest.source.path
        && stat.size === manifest.source.size
        && Math.trunc(stat.mtimeMs) === manifest.source.mtime_ms
        && fs.existsSync(executable)) return executable;
  } catch (error) {
    console.error('Codex permission colours unavailable: ' + error.message);
  }
  return file;
}
function cachedReminderExecutable(file) {
  // Cache hits need only metadata checks in this already-running Node process.
  // Keep Python, hashing, package copying and signing on the cache-miss path.
  try {
    const native = fs.realpathSync(file);
    const stat = fs.statSync(native, { bigint: true });
    const fingerprint = [native, stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs]
      .map(String);
    const state = JSON.parse(fs.readFileSync(path.join(__dirname, 'cache', 'current.json'), 'utf8'));
    if (JSON.stringify(state.launcher_fingerprint) !== JSON.stringify(fingerprint)
        || typeof state.executable !== 'string'
        || !fs.statSync(state.executable).isFile()) return null;
    if (state.executable === native) return native;
    const packageRoot = path.dirname(path.dirname(state.executable));
    if (path.basename(path.dirname(state.executable)) !== 'bin'
        || !fs.statSync(path.join(packageRoot, 'codex-package.json')).isFile()
        || !fs.statSync(path.join(packageRoot, 'codex-resources', 'voice', 'bin', 'codex-voice-host')).isFile()) return null;
    return state.executable;
  } catch {
    return null;
  }
}
childProcess.spawn = function (file, commandArgs, options) {
  let executable = file;
  if (typeof file === 'string' && path.basename(file) === 'codex') {
    executable = selectVoiceFix(file);
    executable = selectPermissionColorsFix(executable);
  }
  if (mute && typeof file === 'string' && path.basename(file) === 'codex') {
    const cached = cachedReminderExecutable(executable);
    if (cached) {
      executable = cached;
    } else {
      const result = childProcess.spawnSync('/usr/bin/python3', [helper, executable], {
        encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
      });
      if (result.status === 0 && result.stdout.trim()) {
        executable = result.stdout.trim();
      } else {
        console.error(result.stderr.trim() || 'Could not prepare the reminder mute; starting the original CLI.');
      }
    }
  }
  return originalSpawn.call(this, executable, commandArgs, options);
};
// Newer npm launchers replace Node directly instead of calling spawn. Keep that
// signal/exit behavior and apply only this version-gated footer patch here.
const originalExecve = process.execve;
if (typeof originalExecve === 'function') {
  process.execve = function (file, commandArgs, environment) {
    const executable = typeof file === 'string' && path.basename(file) === 'codex'
      ? selectPermissionColorsFix(file) : file;
    const args = executable !== file && Array.isArray(commandArgs) && commandArgs[0] === file
      ? [executable, ...commandArgs.slice(1)] : commandArgs;
    return originalExecve.call(this, executable, args, environment);
  };
}
syncBuiltinESMExports();

import(pathToFileURL(upstream).href).catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
