#! /usr/bin/env python3
# Vendored from CPython Tools/i18n/msgfmt.py.
# Written by Martin v. Lowis <loewis@informatik.hu-berlin.de>

"""Generate binary message catalog from textual translation description.

This program converts a textual Uniforum-style message catalog (.po file) into
a binary GNU catalog (.mo file). This is essentially the same function as the
GNU msgfmt program, however, it is a simpler implementation.

Usage: msgfmt.py [OPTIONS] filename.po

Options:
  -o file
  --output-file=file
      Specify the output file to write to. If omitted, output will go to a
      file named filename.mo (based off the input file name).

  -h
  --help
      Print this message and exit.

  -V
  --version
      Display version information and exit.
"""

import array
import ast
import getopt
import os
import struct
import sys
from email.parser import HeaderParser

__version__ = "1.2"

MESSAGES = {}


def usage(code, msg=""):
  print(__doc__, file=sys.stderr)
  if msg:
    print(msg, file=sys.stderr)
  sys.exit(code)


def add(ctxt, msgid, msgstr, fuzzy):
  global MESSAGES
  if not fuzzy and msgstr:
    if ctxt is None:
      MESSAGES[msgid] = msgstr
    else:
      MESSAGES[b"%b\x04%b" % (ctxt, msgid)] = msgstr


def generate():
  global MESSAGES
  keys = sorted(MESSAGES.keys())
  offsets = []
  ids = strs = b""
  for msgid in keys:
    offsets.append((len(ids), len(msgid), len(strs), len(MESSAGES[msgid])))
    ids += msgid + b"\0"
    strs += MESSAGES[msgid] + b"\0"

  keystart = 7 * 4 + 16 * len(keys)
  valuestart = keystart + len(ids)
  koffsets = []
  voffsets = []
  for o1, l1, o2, l2 in offsets:
    koffsets += [l1, o1 + keystart]
    voffsets += [l2, o2 + valuestart]
  offsets = koffsets + voffsets
  output = struct.pack(
    "Iiiiiii",
    0x950412DE,
    0,
    len(keys),
    7 * 4,
    7 * 4 + len(keys) * 8,
    0,
    0,
  )
  output += array.array("i", offsets).tobytes()
  output += ids
  output += strs
  return output


def make(filename, outfile):
  global MESSAGES
  MESSAGES = {}

  id_section = 1
  str_section = 2
  ctxt_section = 3

  infile = filename if filename.endswith(".po") else f"{filename}.po"
  if outfile is None:
    outfile = os.path.splitext(infile)[0] + ".mo"

  try:
    with open(infile, "rb") as f:
      lines = f.readlines()
  except IOError as msg:
    print(msg, file=sys.stderr)
    sys.exit(1)

  section = msgctxt = None
  fuzzy = 0
  encoding = "latin-1"
  is_plural = False
  lno = 0

  for line in lines:
    line = line.decode(encoding)
    lno += 1

    if line[0] == "#" and section == str_section:
      add(msgctxt, msgid, msgstr, fuzzy)
      section = msgctxt = None
      fuzzy = 0

    if line[:2] == "#," and "fuzzy" in line:
      fuzzy = 1

    if line[0] == "#":
      continue

    if line.startswith("msgctxt"):
      if section == str_section:
        add(msgctxt, msgid, msgstr, fuzzy)
      section = ctxt_section
      line = line[7:]
      msgctxt = b""
    elif line.startswith("msgid") and not line.startswith("msgid_plural"):
      if section == str_section:
        if not msgid:
          msgstr = b"".join(
            entry for entry in msgstr.splitlines(True)
            if not entry.startswith(b"POT-Creation-Date:")
          )
          parser = HeaderParser()
          charset = parser.parsestr(msgstr.decode(encoding)).get_content_charset()
          if charset:
            encoding = charset
        add(msgctxt, msgid, msgstr, fuzzy)
        msgctxt = None
      section = id_section
      line = line[5:]
      msgid = msgstr = b""
      is_plural = False
    elif line.startswith("msgid_plural"):
      if section != id_section:
        print(f"msgid_plural not preceded by msgid on {infile}:{lno}", file=sys.stderr)
        sys.exit(1)
      line = line[12:]
      msgid += b"\0"
      is_plural = True
    elif line.startswith("msgstr"):
      section = str_section
      if line.startswith("msgstr["):
        if not is_plural:
          print(f"plural without msgid_plural on {infile}:{lno}", file=sys.stderr)
          sys.exit(1)
        line = line.split("]", 1)[1]
        if msgstr:
          msgstr += b"\0"
      else:
        if is_plural:
          print(f"indexed msgstr required for plural on {infile}:{lno}", file=sys.stderr)
          sys.exit(1)
        line = line[6:]

    line = line.strip()
    if not line:
      continue

    line = ast.literal_eval(line)
    if section == ctxt_section:
      msgctxt += line.encode(encoding)
    elif section == id_section:
      msgid += line.encode(encoding)
    elif section == str_section:
      msgstr += line.encode(encoding)
    else:
      print(f"Syntax error on {infile}:{lno} before:", file=sys.stderr)
      print(line, file=sys.stderr)
      sys.exit(1)

  if section == str_section:
    add(msgctxt, msgid, msgstr, fuzzy)

  try:
    with open(outfile, "wb") as f:
      f.write(generate())
  except IOError as msg:
    print(msg, file=sys.stderr)


def main():
  try:
    opts, args = getopt.getopt(sys.argv[1:], "hVo:", ["help", "version", "output-file="])
  except getopt.error as msg:
    usage(1, msg)

  outfile = None
  for opt, arg in opts:
    if opt in ("-h", "--help"):
      usage(0)
    elif opt in ("-V", "--version"):
      print("msgfmt.py", __version__)
      sys.exit(0)
    elif opt in ("-o", "--output-file"):
      outfile = arg

  if not args:
    print("No input file given", file=sys.stderr)
    print("Try `msgfmt --help' for more information.", file=sys.stderr)
    return

  for filename in args:
    make(filename, outfile)


if __name__ == "__main__":
  main()
