#!/usr/bin/env python
import argparse
import base64
import calendar
import getpass
import json
import os
import pwgen
import subprocess
import sys
import tempfile
import time
from Crypto import Random
from Crypto.Cipher import AES
from Crypto.Hash import HMAC
from Crypto.Hash import SHA256

ALGO_NAME = "aes256-cbc-sha256"
IV_SIZE = AES.block_size
KEY_SIZE = 32

def pad(s):
    padded_len = AES.block_size - len(s) % AES.block_size
    return s + chr(padded_len) * padded_len

def unpad(s):
    return s[0:-ord(s[-1])]

def cipher(key, iv):
    return AES.new(key, AES.MODE_CBC, iv)

def hmac(key):
    return HMAC.new(key, digestmod=SHA256)

def derive_key(key):
    c = AES.new(key, AES.MODE_ECB)
    enc_key = c.encrypt(b'\x00' * len(key))
    hmac_key = c.encrypt(b'\x00' * SHA256.digest_size)
    return (enc_key, hmac_key)

def enc(enc_key, hmac_key, plaintext=None):
    iv = Random.new().read(IV_SIZE)
    h = hmac(hmac_key)
    h.update(plaintext)
    hmac_tag = base64.b64encode(h.digest())
    ciphertext = base64.b64encode(cipher(enc_key, iv).encrypt(pad(plaintext)))
    return dict(algorithm=ALGO_NAME,
        timestamp=calendar.timegm(time.gmtime()),
        iv=base64.b64encode(iv),
        hmac=hmac_tag,
        text=ciphertext)

def dec(enc_key, hmac_key, data={}):
    iv = base64.b64decode(data['iv'])
    ciphertext = base64.b64decode(data['text'])
    plaintext = unpad(cipher(enc_key, iv).decrypt(ciphertext))
    h = hmac(hmac_key)
    h.update(plaintext)
    if h.digest() != base64.b64decode(data['hmac']):
        raise NameError('HMAC mismatch')
    return plaintext

def show(s):
    if s is not None:
        sys.stdout.write(s)
        if sys.stdout.isatty():
            sys.stdout.write('\n')

def generate_key():
    return Random.new().read(KEY_SIZE)

class Keychain(object):
    USERNAME_FIELD = 'username'
    PASSWORD_FIELD = 'password'
    NOTE_FIELD = 'note'

    def __init__(self):
        self.root = {}
        self.dirty = False
        self.key = None

    def is_dirty(self):
        return self.dirty

    def use_key(self, key):
        if key is None:
            raise NameError('key must not be None')

        if len(key) != KEY_SIZE:
            raise NameError("key size must be %d bits long" % (KEY_SIZE * 8))

        self.key = key

    def load(self, filename):
        if self.dirty:
            raise NameError('load must not be called while keychain is dirty')

        self.root = json.load(open(filename))

    def save(self, filename, pretty=True):
        if not self.dirty:
            return

        if pretty:
            params = {'sort_keys':True, 'indent':4, 'separators':(',', ': ')}
        else:
            params = {}

        f = open(filename, "w")
        j = json.dumps(self.root, **params)
        f.write(j)
        f.close()
        self.dirty = False

    def get(self, node_name, field_name):
        if node_name not in self.root:
            return None

        node = self.root[node_name]
        if field_name not in node:
            return None

        enc_text_node = node[field_name]
        return dec(*derive_key(self.key), data=enc_text_node)

    def set(self, node_name, field_name, value):
        if node_name in self.root:
            node = self.root[node_name]
        else:
            node = {}

        node[field_name] = enc(*derive_key(self.key), plaintext=value)
        self.root[node_name] = node
        self.dirty = True

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--file', metavar='FILE', dest='file', help='data file')

    parser.add_argument(
        '--generate-key-to-stdout', dest='generate_key_to_stdout',
        action='store_true')

    parser.add_argument(
        '--key', metavar='KEY', dest='key', help='AES key')

    parser.add_argument(
        '--key-from-stdin', dest='key_from_stdin', action='store_true')

    parser.add_argument(
        '--node', dest='node')

    parser.add_argument(
        '--get-password', dest='get_password', action='store_true')

    parser.add_argument(
        '--set-password', dest='set_password', action='store_true')

    parser.add_argument(
        '--generate-and-set-password', metavar='LENGTH',
        dest='generate_and_set_password')

    parser.add_argument(
        '--get-username', dest='get_username', action='store_true')

    parser.add_argument(
        '--set-username', dest='set_username')

    parser.add_argument(
        '--show-note', dest='show_note', action='store_true')

    parser.add_argument(
        '--edit-note', dest='edit_note', action='store_true')

    parser.add_argument(
        '--dump', dest='dump', action='store_true')

    parser.add_argument(
        '--list-nodes', dest='list_nodes', action='store_true')

    parser.add_argument(
        '--verbose', '-v', action='store_true')

    args = parser.parse_args()

    if args.generate_key_to_stdout:
        sys.stdout.write(generate_key())
        return 0

    key = None

    if args.key_from_stdin:
        key = sys.stdin.read()

    if args.key:
        if os.path.exists(args.key):
            key = open(args.key).read()
        else:
            sys.stderr.write('no such key file\n')
            return 1

    if key is None:
        parser.print_help()
        return 1

    if args.file is None:
        sys.stderr.write('no file specified\n')
        return 1

    keychain = Keychain()
    keychain.use_key(key)

    if os.path.exists(args.file):
        keychain.load(args.file)

    if args.dump:
        node_names = keychain.root.keys()
        for n in node_names:
            username = keychain.get(n, Keychain.USERNAME_FIELD)
            password = keychain.get(n, Keychain.PASSWORD_FIELD)
            print("%s\t%s\t%s" % (n, username, password))
        return 0

    if args.list_nodes:
        for node_name in keychain.root:
            print(node_name)
        return 0

    if args.node is None:
        sys.stderr.write('no node specified\n')
        return 1

    if args.get_username:
        show(keychain.get(args.node, Keychain.USERNAME_FIELD))
        return 0

    if args.get_password:
        show(keychain.get(args.node, Keychain.PASSWORD_FIELD))
        return 0

    if args.show_note:
        show(keychain.get(args.node, Keychain.NOTE_FIELD))
        return 0

    if args.generate_and_set_password:
        password = pwgen.pwgen(
            int(args.generate_and_set_password),
            capitalize=True,
            allowed_symbols=',.;!-')
        keychain.set(args.node, Keychain.PASSWORD_FIELD, password)

    if args.set_password:
        # READ FROM CMD LINE
        p1 = getpass.getpass('enter password: ')
        p2 = getpass.getpass('repeat password: ')

        if p1 != p2:
            sys.stderr.write('password does not match\n')
            return 1

        if len(p1) == 0 or len(p2) == 0:
            sys.stderr.write('password must not be empty\n')
            return 1

        keychain.set(args.node, Keychain.PASSWORD_FIELD, p1)

    if args.set_username:
        keychain.set(args.node, Keychain.USERNAME_FIELD, args.set_username)

    if args.edit_note:
        tf = tempfile.NamedTemporaryFile(delete=False, dir=os.getcwd())

        old_note = keychain.get(args.node, Keychain.NOTE_FIELD)
        if old_note is None:
            old_note = ''

        tf.write(old_note)
        tf.close()
        tfn = os.path.join(os.getcwd(), tf.name)
        tf.close()

        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
        p = subprocess.Popen("%s \"%s\"" % (editor, tfn), shell=True)
        p.wait()

        tf = open(tfn)
        new_note = tf.read()
        tf.close()
        os.unlink(tfn)

        if old_note != new_note:
            keychain.set(args.node, Keychain.NOTE_FIELD, new_note)
            sys.stderr.write('note updated\n')
        else:
            sys.stderr.write('no change to note\n')

    if keychain.is_dirty():
        if not os.path.exists(args.file):
            sys.stderr.write('creating new file\n')

        keychain.save(args.file)

    return 0
