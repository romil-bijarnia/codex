#!/usr/bin/env python3
"""Apply the configured base and personal updates to loaded chats, without a turn.

Audits by default. --apply appends native model-visible context updates through
the existing app server. It does not restart chats, change goals or run work.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
import tomllib


class Client:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(12)
        self.sock.connect(str(path))
        self.buffer = b''
        self.sequence = 0
        key = base64.b64encode(os.urandom(16)).decode()
        request = ('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key +
                   '\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode())
        while b'\r\n\r\n' not in self.buffer:
            self.buffer += self.sock.recv(4096)
            if len(self.buffer) > 65536:
                raise RuntimeError('Invalid WebSocket handshake')
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        accept = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if b'101 Switching Protocols' not in header or accept not in header:
            raise RuntimeError('App-server WebSocket handshake failed')
        self.rpc('initialize', {
            'clientInfo': {'name': 'romil_prompt_restoration', 'version': '1.0.0'},
            'capabilities': {'experimentalApi': True}})
        self.send({'jsonrpc': '2.0', 'method': 'initialized'})

    def read(self, length):
        while len(self.buffer) < length:
            chunk = self.sock.recv(max(4096, length - len(self.buffer)))
            if not chunk:
                raise RuntimeError('App server disconnected')
            self.buffer += chunk
        data, self.buffer = self.buffer[:length], self.buffer[length:]
        return data

    def send(self, value, opcode=1):
        payload = json.dumps(value).encode() if opcode == 1 else value
        length = len(payload)
        mask = os.urandom(4)
        if length < 126:
            header = bytes([128 | opcode, 128 | length])
        elif length < 65536:
            header = bytes([128 | opcode, 254]) + struct.pack('!H', length)
        else:
            header = bytes([128 | opcode, 255]) + struct.pack('!Q', length)
        self.sock.sendall(header + mask + bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload)))

    def receive(self):
        fragments = b''
        while True:
            first, second = self.read(2)
            opcode, length = first & 15, second & 127
            if length == 126:
                length = struct.unpack('!H', self.read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.read(8))[0]
            if length > 32 * 1024 * 1024:
                raise RuntimeError('Unexpectedly large app-server frame')
            mask = self.read(4) if second & 128 else None
            payload = self.read(length)
            if mask:
                payload = bytes(value ^ mask[index % 4]
                                for index, value in enumerate(payload))
            if opcode == 9:
                self.send(payload, 10)
                continue
            if opcode == 8:
                raise RuntimeError('App server closed the connection')
            if opcode not in (0, 1):
                continue
            fragments += payload
            if first & 128:
                return json.loads(fragments)

    def rpc(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        self.send({'jsonrpc': '2.0', 'id': identifier,
                   'method': method, 'params': params})
        while True:
            response = self.receive()
            if response.get('id') == identifier:
                if 'error' in response:
                    raise RuntimeError(json.dumps(response['error']))
                return response['result']


def discover_socket():
    prefix = f'/tmp/codex-daemon-{os.getuid()}/'
    candidates = set()
    for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) > 7 and fields[3] == '00010000' and fields[7].startswith(prefix):
            candidates.add(fields[7])
    if len(candidates) != 1:
        raise RuntimeError('Specify --socket for the intended running app server')
    return next(iter(candidates))


def history_state(path):
    instructions, agents, execution_rules = None, '', ''
    try:
        history = Path(path).open()
    except FileNotFoundError:
        # A loaded thread may not have a persisted rollout yet. Its update can
        # still be accepted in memory; keep readback pending rather than claiming
        # persistence or preventing every other loaded chat from updating.
        return None, '', ''
    for line in history:
        item = json.loads(line)
        payload = item.get('payload', {})
        if item.get('type') == 'session_meta':
            instructions = payload.get('base_instructions')
            if isinstance(instructions, dict):
                instructions = instructions.get('text')
        if item.get('type') != 'response_item':
            continue
        text = '\n'.join(c.get('text', '') for c in payload.get('content', [])
                         if isinstance(c, dict) and isinstance(c.get('text'), str))
        if payload.get('role') == 'developer' and '<model_instructions>' in text:
            marker = 'The following instructions replace the previous base instructions:'
            if marker in text:
                instructions = text.split(marker, 1)[1].split('</model_instructions>', 1)[0].strip()
            elif 'No replacement base instructions are active.' in text:
                instructions = ''
        if payload.get('role') == 'user' and text.startswith('# AGENTS.md instructions'):
            agents = text
        if payload.get('role') == 'developer':
            if '<personal_execution_rules_removed>' in text:
                execution_rules = ''
            elif '<personal_execution_rules>' in text and '</personal_execution_rules>' in text:
                execution_rules = '<personal_execution_rules>' + text.split(
                    '<personal_execution_rules>', 1)[1].split('</personal_execution_rules>', 1)[0]
                execution_rules += '</personal_execution_rules>'
    return instructions, agents, execution_rules


def message(role, text, kind=None):
    item = {'type': 'message', 'role': role,
            'content': [{'type': 'input_text', 'text': text}]}
    if kind:
        item['internal_chat_message_metadata_passthrough'] = {
            'content_item_kinds': [kind]}
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    models = json.loads((home / 'models_cache.json').read_text())['models']
    catalog = {model['slug']: model for model in models}
    personal = (home / 'AGENTS.md').read_text().strip()
    config = tomllib.loads((home / 'config.toml').read_text())
    developer = config.get('developer_instructions', '').strip()
    rules = ''
    if '<personal_execution_rules>' in developer:
        rules = '<personal_execution_rules>' + developer.split(
            '<personal_execution_rules>', 1)[1].split('</personal_execution_rules>', 1)[0]
        rules += '</personal_execution_rules>'
    client = Client(args.socket or discover_socket())
    results = []
    try:
        loaded = client.rpc('thread/loaded/list', {})
        if loaded.get('nextCursor'):
            raise RuntimeError('Loaded thread pagination requires explicit handling')
        for identifier in loaded['data']:
            thread = client.rpc('thread/read', {
                'threadId': identifier, 'includeTurns': False})['thread']
            if thread.get('canAcceptDirectInput') is False:
                results.append({'thread_id': identifier, 'parent_managed_subagent': True,
                                'skipped_direct_update': True, 'status_before': thread['status']})
                continue
            model = thread['model']
            if config.get('model_instructions_file'):
                base_path = Path(config['model_instructions_file']).expanduser()
                if not base_path.is_absolute():
                    base_path = Path(thread['cwd']) / base_path
                base_text = base_path.read_text().strip()
            elif 'instructions' in config:
                base_text = config['instructions'].strip()
            else:
                base_text = catalog[model]['model_messages']['instructions_template'].strip()
            current, previous_agents, previous_rules = history_state(thread['path'])
            items = []
            if current is None or current.strip() != base_text:
                items.append(message('developer', '<model_instructions>\n'
                    f'The current model is {model}.\n'
                    + ('The following instructions replace the previous base instructions:\n\n'
                       + base_text if base_text else
                       'The previous base instructions no longer apply. No replacement base instructions are active.')
                    + '\n</model_instructions>', 'model.instructions'))
            if rules != previous_rules:
                if rules:
                    items.append(message('developer',
                        'The following personal execution rules replace any earlier personal execution rules.\n\n'
                        + developer, 'developer.instructions'))
                else:
                    items.append(message('developer',
                        '<personal_execution_rules_removed>\n'
                        'The previous personal execution rules no longer apply. '
                        'The configured base and AGENTS.md remain active.\n'
                        '</personal_execution_rules_removed>', 'developer.instructions'))
            if personal not in previous_agents:
                items.append(message('user', '# AGENTS.md instructions\n\n<INSTRUCTIONS>\n'
                    'These global AGENTS.md instructions replace earlier global AGENTS.md instructions. '
                    'Keep applicable project instructions and all current task constraints.\n\n'
                    + personal + '\n</INSTRUCTIONS>'))
            record = {'thread_id': identifier, 'model': model,
                      'updates_needed': len(items), 'status_before': thread['status'],
                      'history_file_missing_before': not Path(thread['path']).exists()}
            if items and args.apply:
                client.rpc('thread/inject_items', {'threadId': identifier, 'items': items})
                record['native_update_accepted'] = True
                # Rollout persistence trails the accepted in-memory update briefly.
                # Poll the receipt's readback; never repeat the mutation to wait.
                record['verified_persisted'] = False
                for _ in range(30):
                    after, after_agents, after_rules = history_state(thread['path'])
                    if (after is not None and after.strip() == base_text
                            and personal in after_agents and after_rules == rules):
                        record['verified_persisted'] = True
                        break
                    time.sleep(0.05)
                if not record['verified_persisted']:
                    record['persistence_pending'] = True
            elif not items:
                record['verified_persisted'] = True
            results.append(record)
    finally:
        client.sock.close()
    print(json.dumps({'applied': args.apply, 'chats': results,
                      'base_mode': 'file' if config.get('model_instructions_file') else 'inline' if 'instructions' in config else 'stock',
                      'personal_execution_rules_configured': bool(rules),
                      'new_turns_started': 0, 'model_requests': 0,
                      'pending_readback_requires_audit_not_repeat_apply':
                          any(r.get('persistence_pending') for r in results),
                      'archived_or_unloaded_histories_modified': False}, indent=2))


if __name__ == '__main__':
    main()
