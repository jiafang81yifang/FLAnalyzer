"""R6: remove the post-repair re-render calls that span several lines."""

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / 'app_v84_hermes.py'
lines = TARGET.read_text(encoding='utf-8').splitlines(keepends=True)

out: list[str] = []
i = 0
removed = 0
while i < len(lines):
    ln = lines[i]
    if '_apply_rendered_pack_tables(' in ln and not ln.lstrip().startswith('def '):
        # consume the whole call, however many lines it spans, up to the line
        # whose stripped form closes it with ')'
        depth = ln.count('(') - ln.count(')')
        i += 1
        while depth > 0 and i < len(lines):
            depth += lines[i].count('(') - lines[i].count(')')
            i += 1
        removed += 1
        continue
    out.append(ln)
    i += 1

print('removed calls:', removed)
TARGET.write_text(''.join(out), encoding='utf-8')
