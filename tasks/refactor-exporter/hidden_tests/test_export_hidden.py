import ast
import contextlib
import io
from pathlib import Path

import pytest
from export import export, main

# Expected values were produced by the original export.py before it was refactored.

CASES = [('text-default',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {},
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Alice   30    9.5  yes\n'
  'Bob     25      3  no      a; b\n'
  'Zoë                              line1 line2\n'),
 ('csv-default',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'csv'},
  'name,age,score,active,tags,note\n'
  'Alice,30,9.5,true,,\n'
  'Bob,25,3.0,false,a|b,\n'
  'Zoë,,,,,"line1\n'
  'line2"\n'),
 ('markdown-default',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'markdown'},
  '| name | age | score | active | tags | note |\n'
  '| --- | ---: | ---: | --- | --- | --- |\n'
  '| Alice | 30 | 9.5 | yes |  |  |\n'
  '| Bob | 25 | 3 | no | a; b |  |\n'
  '| Zoë |  |  |  |  | line1<br>line2 |\n'),
 ('text-subset-and-missing-column',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'columns': ['score', 'name', 'nope']},
  'score  name   nope\n-----  -----  ----\n  9.5  Alice\n    3  Bob\n       Zoë\n'),
 ('text-sort-numbers-none-last',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'sort_by': 'age'},
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Bob     25      3  no      a; b\n'
  'Alice   30    9.5  yes\n'
  'Zoë                              line1 line2\n'),
 ('text-sort-reverse-none-last',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'sort_by': 'age', 'reverse': True},
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Alice   30    9.5  yes\n'
  'Bob     25      3  no      a; b\n'
  'Zoë                              line1 line2\n'),
 ('csv-sort-by-name',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'csv', 'sort_by': 'name', 'reverse': True},
  'name,age,score,active,tags,note\n'
  'Zoë,,,,,"line1\n'
  'line2"\n'
  'Bob,25,3.0,false,a|b,\n'
  'Alice,30,9.5,true,,\n'),
 ('markdown-sort-by-bool',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'markdown', 'sort_by': 'active'},
  '| name | age | score | active | tags | note |\n'
  '| --- | ---: | ---: | --- | --- | --- |\n'
  '| Bob | 25 | 3 | no | a; b |  |\n'
  '| Alice | 30 | 9.5 | yes |  |  |\n'
  '| Zoë |  |  |  |  | line1<br>line2 |\n'),
 ('text-truncation',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'max_width': 5},
  'name   age  score  acti…  tags  note\n'
  '-----  ---  -----  -----  ----  -----\n'
  'Alice   30    9.5  yes\n'
  'Bob     25      3  no     a; b\n'
  'Zoë                             line…\n'),
 ('text-truncation-width-two',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'max_width': 2},
  'n…  a…  s…  a…  t…  n…\n'
  '--  --  --  --  --  --\n'
  'A…  30  9…  y…\n'
  'B…  25   3  no  a…\n'
  'Z…                  l…\n'),
 ('text-no-header',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'header': False},
  'Alice   30    9.5  yes\n'
  'Bob     25      3  no      a; b\n'
  'Zoë                              line1 line2\n'),
 ('csv-no-header',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'csv', 'header': False},
  'Alice,30,9.5,true,,\nBob,25,3.0,false,a|b,\nZoë,,,,,"line1\nline2"\n'),
 ('markdown-no-header',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'markdown', 'header': False},
  ['ValueError', 'markdown tables need a header']),
 ('text-null-text',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'null': '-'},
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Alice   30    9.5  yes     -     -\n'
  'Bob     25      3  no      a; b  -\n'
  'Zoë      -      -  -       -     line1 line2\n'),
 ('markdown-null-text',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'markdown', 'null': 'n/a'},
  '| name | age | score | active | tags | note |\n'
  '| --- | ---: | ---: | --- | --- | --- |\n'
  '| Alice | 30 | 9.5 | yes | n/a | n/a |\n'
  '| Bob | 25 | 3 | no | a; b | n/a |\n'
  '| Zoë | n/a | n/a | n/a | n/a | line1<br>line2 |\n'),
 ('csv-ignores-null-text',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'csv', 'null': 'n/a'},
  'name,age,score,active,tags,note\n'
  'Alice,30,9.5,true,,\n'
  'Bob,25,3.0,false,a|b,\n'
  'Zoë,,,,,"line1\n'
  'line2"\n'),
 ('text-float-digits',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'float_digits': 1},
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Alice   30  1e+01  yes\n'
  'Bob     25      3  no      a; b\n'
  'Zoë                              line1 line2\n'),
 ('markdown-float-digits',
  [{'n': 1234567.0, 'i': 1000000000000, 'f': 0.30000000000000004, 's': 'x'},
   {'n': -0.5, 'i': 7, 'f': 2.0, 's': 'yy'}],
  {'fmt': 'markdown', 'float_digits': 3},
  '| n | i | f | s |\n'
  '| ---: | ---: | ---: | --- |\n'
  '| 1.23e+06 | 1000000000000 | 0.3 | x |\n'
  '| -0.5 | 7 | 2 | yy |\n'),
 ('csv-quoting',
  [{'a': 'plain', 'b': 'has,comma', 'c': 'say "hi"'},
   {'a': ' padded ', 'b': 'two\nlines', 'c': 'tab\there'},
   {'a': '', 'b': None, 'c': [1, None, 'x,y']}],
  {'fmt': 'csv'},
  'a,b,c\nplain,"has,comma","say ""hi"""\n" padded ","two\nlines",tab\there\n,,"1||x,y"\n'),
 ('text-quotes-and-newlines',
  [{'a': 'plain', 'b': 'has,comma', 'c': 'say "hi"'},
   {'a': ' padded ', 'b': 'two\nlines', 'c': 'tab\there'},
   {'a': '', 'b': None, 'c': [1, None, 'x,y']}],
  {'max_width': 8},
  'a         b         c\n'
  '--------  --------  --------\n'
  'plain     has,com…  say "hi"\n'
  ' padded   two lin…  tab\there\n'
  '                    1; ; x,y\n'),
 ('markdown-escapes',
  [{'a': 'x|y', 'b': 'l1\nl2\r\nl3\rl4'}, {'a': [1.5, True, None, ['n', 2]], 'b': ('t', 1.0)}],
  {'fmt': 'markdown'},
  '| a | b |\n| --- | --- |\n| x\\|y | l1<br>l2<br>l3<br>l4 |\n| 1.5; yes; ; n; 2 | t; 1 |\n'),
 ('text-lists-and-line-breaks',
  [{'a': 'x|y', 'b': 'l1\nl2\r\nl3\rl4'}, {'a': [1.5, True, None, ['n', 2]], 'b': ('t', 1.0)}],
  {},
  'a                 b\n'
  '----------------  ------------\n'
  'x|y               l1 l2  l3 l4\n'
  '1.5; yes; ; n; 2  t; 1\n'),
 ('csv-lists',
  [{'a': 'x|y', 'b': 'l1\nl2\r\nl3\rl4'}, {'a': [1.5, True, None, ['n', 2]], 'b': ('t', 1.0)}],
  {'fmt': 'csv'},
  'a,b\nx|y,"l1\nl2\r\nl3\rl4"\n1.5|true||n|2,t|1.0\n'),
 ('text-numbers',
  [{'n': 1234567.0, 'i': 1000000000000, 'f': 0.30000000000000004, 's': 'x'},
   {'n': -0.5, 'i': 7, 'f': 2.0, 's': 'yy'}],
  {},
  '          n              i    f  s\n'
  '-----------  -------------  ---  --\n'
  '1.23457e+06  1000000000000  0.3  x\n'
  '       -0.5              7    2  yy\n'),
 ('csv-numbers',
  [{'n': 1234567.0, 'i': 1000000000000, 'f': 0.30000000000000004, 's': 'x'},
   {'n': -0.5, 'i': 7, 'f': 2.0, 's': 'yy'}],
  {'fmt': 'csv'},
  'n,i,f,s\n1234567.0,1000000000000,0.30000000000000004,x\n-0.5,7,2.0,yy\n'),
 ('markdown-numbers',
  [{'n': 1234567.0, 'i': 1000000000000, 'f': 0.30000000000000004, 's': 'x'},
   {'n': -0.5, 'i': 7, 'f': 2.0, 's': 'yy'}],
  {'fmt': 'markdown'},
  '| n | i | f | s |\n'
  '| ---: | ---: | ---: | --- |\n'
  '| 1.23457e+06 | 1000000000000 | 0.3 | x |\n'
  '| -0.5 | 7 | 2 | yy |\n'),
 ('sort-is-stable',
  [{'k': 2, 'id': 'a'}, {'k': 1, 'id': 'b'}, {'k': 2, 'id': 'c'}, {'k': 1, 'id': 'd'}, {'id': 'e'}],
  {'fmt': 'csv', 'sort_by': 'k'},
  'k,id\n1,b\n1,d\n2,a\n2,c\n,e\n'),
 ('sort-is-stable-reversed',
  [{'k': 2, 'id': 'a'}, {'k': 1, 'id': 'b'}, {'k': 2, 'id': 'c'}, {'k': 1, 'id': 'd'}, {'id': 'e'}],
  {'fmt': 'csv', 'sort_by': 'k', 'reverse': True},
  'k,id\n2,a\n2,c\n1,b\n1,d\n,e\n'),
 ('sort-mixed-types',
  [{'v': 3}, {'v': 'a'}, {'v': 1.5}, {'v': 'B'}, {'v': None}, {'v': True}, {'v': -2}],
  {'fmt': 'csv', 'sort_by': 'v'},
  'v\n-2\n1.5\n3\nB\ntrue\na\n\n'),
 ('sort-mixed-types-reversed',
  [{'v': 3}, {'v': 'a'}, {'v': 1.5}, {'v': 'B'}, {'v': None}, {'v': True}, {'v': -2}],
  {'fmt': 'csv', 'sort_by': 'v', 'reverse': True},
  'v\na\ntrue\nB\n3\n1.5\n-2\n\n'),
 ('text-mixed-column-is-left-aligned',
  [{'v': 3}, {'v': 'a'}, {'v': 1.5}, {'v': 'B'}, {'v': None}, {'v': True}, {'v': -2}],
  {},
  'v\n---\n3\na\n1.5\nB\n\nyes\n-2\n'),
 ('text-empty-rows-with-columns', [], {'columns': ['a', 'bb']}, 'a  bb\n-  --\n'),
 ('csv-empty-rows-with-columns', [], {'fmt': 'csv', 'columns': ['a', 'bb']}, 'a,bb\n'),
 ('markdown-empty-rows-with-columns',
  [],
  {'fmt': 'markdown', 'columns': ['a', 'bb']},
  '| a | bb |\n| --- | --- |\n'),
 ('empty-rows-no-columns', [], {}, ''),
 ('empty-column-list',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'columns': []},
  ''),
 ('all-none-column', [{'a': None, 'b': 1}, {'a': None, 'b': 2}], {}, 'a  b\n-  -\n   1\n   2\n'),
 ('header-wider-than-values', [{'long header': 1}], {}, 'long header\n-----------\n          1\n'),
 ('trailing-spaces-are-trimmed',
  [{'a': 'x', 'b': ''}, {'a': 'yyyy', 'b': ''}],
  {},
  'a     b\n----  -\nx\nyyyy\n'),
 ('unicode-ellipsis', [{'t': 'abcdefghij'}], {'max_width': 6}, 't\n------\nabcde…\n'),
 ('duplicate-columns',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'columns': ['name', 'name']},
  ['ValueError', 'duplicate column names']),
 ('unknown-sort-column',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'sort_by': 'zzz'},
  ['ValueError', "cannot sort by unknown column 'zzz'"]),
 ('unknown-sort-column-with-no-columns',
  [],
  {'sort_by': 'zzz'},
  ['ValueError', "cannot sort by unknown column 'zzz'"]),
 ('unknown-format',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'fmt': 'xml'},
  ['ValueError', "unknown format 'xml'; expected text, csv or markdown"]),
 ('max-width-too-small',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'max_width': 1},
  ['ValueError', 'max_width must be at least 2']),
 ('sort-by-column-not-shown',
  [{'name': 'Alice', 'age': 30, 'score': 9.5, 'active': True},
   {'name': 'Bob', 'age': 25, 'score': 3.0, 'active': False, 'tags': ['a', 'b']},
   {'name': 'Zoë', 'age': None, 'note': 'line1\nline2'}],
  {'columns': ['name'], 'sort_by': 'age'},
  ['ValueError', "cannot sort by unknown column 'age'"])]

MAIN_CASES = [('main-default',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  [],
  0,
  'name   age  score  active  tags  note\n'
  '-----  ---  -----  ------  ----  -----------\n'
  'Alice   30    9.5  yes\n'
  'Bob     25      3  no      a; b\n'
  'Zoë                              line1 line2\n',
  ''),
 ('main-csv-sorted',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['-f', 'csv', '--sort', 'name', '--reverse'],
  0,
  'name,age,score,active,tags,note\n'
  'Zoë,,,,,"line1\n'
  'line2"\n'
  'Bob,25,3.0,false,a|b,\n'
  'Alice,30,9.5,true,,\n',
  ''),
 ('main-markdown-columns',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['--format', 'markdown', '--columns', 'name,score'],
  0,
  '| name | score |\n| --- | ---: |\n| Alice | 9.5 |\n| Bob | 3 |\n| Zoë |  |\n',
  ''),
 ('main-no-header-null-width',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['--no-header', '--null', '?', '--max-width', '4'],
  0,
  'Ali…   30   9.5  yes   ?     ?\n'
  'Bob    25     3  no    a; b  ?\n'
  'Zoë     ?     ?  ?     ?     lin…\n',
  ''),
 ('main-unknown-sort-column',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['--sort', 'zzz'],
  1,
  '',
  "error: cannot sort by unknown column 'zzz'\n"),
 ('main-markdown-no-header',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['-f', 'markdown', '--no-header'],
  1,
  '',
  'error: markdown tables need a header\n'),
 ('main-invalid-json', '{not json', [], 1, '', 'error: input is not valid JSON ('),
 ('main-not-a-list', '{"a": 1}', [], 1, '', 'error: input must be a JSON list of objects\n'),
 ('main-list-of-non-objects', '[1, 2]', [], 1, '', 'error: input must be a JSON list of objects\n'),
 ('main-empty-list', '[]', [], 0, '', ''),
 ('main-bad-usage',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['--format', 'xml'],
  2,
  '',
  ''),
 ('main-bad-width',
  '[{"name": "Alice", "age": 30, "score": 9.5, "active": true}, {"name": "Bob", "age": 25, '
  '"score": 3.0, "active": false, "tags": ["a", "b"]}, {"name": "Zo\\u00eb", "age": null, "note": '
  '"line1\\nline2"}]',
  ['--max-width', 'x'],
  2,
  '',
  '')]


def run(rows, kwargs):
    try:
        return export(rows, **kwargs)
    except ValueError as exc:
        return ["ValueError", str(exc)]


@pytest.mark.parametrize(("case_id", "rows", "kwargs", "expected"), CASES, ids=[c[0] for c in CASES])
def test_export_behaves_exactly_as_before(case_id, rows, kwargs, expected):
    assert run(rows, kwargs) == expected


# errors and empty results are export's own: the renderers only see real tables
RENDERED = [case for case in CASES if isinstance(case[3], str) and case[3] != ""]


@pytest.mark.parametrize(
    ("case_id", "rows", "kwargs", "expected"), RENDERED, ids=[c[0] for c in RENDERED]
)
def test_the_new_functions_compose_to_the_same_result(case_id, rows, kwargs, expected):
    from columns import resolve_columns, sort_rows
    from formatters import format_csv, format_markdown, format_text

    names = resolve_columns(rows, kwargs.get("columns"))
    ordered = sort_rows(rows, names, kwargs.get("sort_by"), kwargs.get("reverse", False))
    header = kwargs.get("header", True)
    null = kwargs.get("null", "")
    digits = kwargs.get("float_digits", 6)
    match kwargs.get("fmt", "text"):
        case "csv":
            result = format_csv(ordered, columns=names, header=header)
        case "markdown":
            result = format_markdown(
                ordered, columns=names, header=header, null=null, float_digits=digits
            )
        case _:
            result = format_text(
                ordered,
                columns=names,
                max_width=kwargs.get("max_width", 20),
                header=header,
                null=null,
                float_digits=digits,
            )
    assert result == expected


def test_resolve_columns_lists_every_key_in_order_of_appearance():
    from columns import resolve_columns

    rows = [{"b": 1, "a": 2}, {"c": 3, "b": 4}]
    assert resolve_columns(rows, None) == ["b", "a", "c"]
    assert resolve_columns(rows, ["c", "b"]) == ["c", "b"]
    assert resolve_columns([], None) == []


def test_resolve_columns_refuses_duplicates():
    from columns import resolve_columns

    with pytest.raises(ValueError, match="duplicate column names"):
        resolve_columns([{"a": 1}], ["a", "a"])


def test_sort_rows_returns_the_sorted_list_and_keeps_none_last():
    from columns import sort_rows

    rows = [{"v": 2}, {"v": None}, {"v": 1}, {}]
    assert sort_rows(rows, ["v"], "v", False) == [{"v": 1}, {"v": 2}, {"v": None}, {}]
    assert sort_rows(rows, ["v"], "v", True) == [{"v": 2}, {"v": 1}, {"v": None}, {}]
    assert sort_rows(rows, ["v"], None, True) == rows
    assert rows[0] == {"v": 2}  # the argument is not reordered


def test_sort_rows_refuses_an_unknown_column():
    from columns import sort_rows

    with pytest.raises(ValueError, match="cannot sort by unknown column 'zzz'"):
        sort_rows([{"a": 1}], ["a"], "zzz", False)


def code_lines(path):
    """Lines that are not blank, comments or docstrings."""
    text = path.read_text()
    skipped = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                skipped.update(range(first.lineno, first.end_lineno + 1))
    return sum(
        1
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#") and number not in skipped
    )


def test_export_py_is_at_most_90_lines_of_code():
    import export as export_module

    assert code_lines(Path(export_module.__file__)) <= 90


@pytest.mark.parametrize(("case_id", "stdin", "argv", "code", "out", "err"), MAIN_CASES, ids=[c[0] for c in MAIN_CASES])
def test_main_behaves_exactly_as_before(case_id, stdin, argv, code, out, err):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):  # argparse reports usage errors itself
        status = main(argv, io.StringIO(stdin), stdout, stderr)
    assert status == code
    assert stdout.getvalue() == out
    if case_id == "main-invalid-json":
        assert stderr.getvalue().startswith(err)
    else:
        assert stderr.getvalue() == err
