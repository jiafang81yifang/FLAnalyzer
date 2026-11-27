#!/bin/bash
# Revert each fix to its stub form and prove the guarding tests go red.
#
# The working tree must be clean: every revert is applied on top of HEAD and
# discarded with `git checkout -- .` (never `git checkout <file>` on an
# uncommitted tree, which silently discards unrelated work).
set -u
cd /agent/repos/FLAnalyzer
PY=python3
fail=0

if [ -n "$(git status --porcelain)" ]; then
  echo "REFUSING TO RUN: working tree is dirty. Commit first."
  git status --short
  exit 2
fi

run() {  # run <label> <pytest args...>
  local label="$1"; shift
  $PY -m pytest "$@" -q >/tmp/rev.log 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo "RED   $label  (exit $rc, expected)"
    grep -E '^[0-9]+ (failed|passed)' /tmp/rev.log | tail -1 | sed 's/^/        /'
  else
    echo "GREEN $label  <-- TEST DOES NOT HAVE TEETH"
    grep -E '^[0-9]+ (failed|passed)' /tmp/rev.log | tail -1 | sed 's/^/        /'
    fail=1
  fi
}

echo "=== BASELINE (fixed code, tests must be green) ==="
$PY -m pytest tests/test_gate_teeth.py -q >/tmp/rev.log 2>&1 \
  && echo "GREEN baseline ok" || { echo "RED baseline — fix first"; exit 1; }

echo
echo "=== R1: restore format_matrix_dual_prejudge_html -> '' ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
start=s.index('    def format_matrix_dual_prejudge_html(text: Any')
end=s.index('    @staticmethod', s.index('return rendered if rendered.strip()', start))
s=s[:start]+'    def format_matrix_dual_prejudge_html(*args: Any, **kwargs: Any) -> str:\n        return \'\'\n\n'+s[end:]
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R1 renderer stub" tests/test_gate_teeth.py -k "html_renderer or empty_render_falls_back or real_renderer_output"
git checkout -- .

echo
echo "=== R2: restore ensure_judgment_standard_sections -> str ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
start=s.index('    def ensure_judgment_standard_sections(text: Any')
end=s.index('    @staticmethod', start)
s=s[:start]+'    def ensure_judgment_standard_sections(text: Any) -> str:\n        return str(text or \'\')\n\n'+s[end:]
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R2 gate stub" tests/test_gate_teeth.py -k "gate or production_gate"
git checkout -- .

echo
echo "=== R3: restore _tg_pushed = True unconditionally ==="
$PY - <<'PYEOF'
p='app_v84_hermes.py'
s=open(p,encoding='utf-8').read()
old="""            _tg_pushed = bool(str(_tg_html or '').strip())
            if _tg_pushed:"""
new="""            _tg_pushed = True
            if _tg_pushed:"""
assert old in s, 'anchor not found'
s=s.replace(old,new)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R3 _tg_pushed unconditional" tests/test_gate_teeth.py -k "tg_pushed"
git checkout -- .

echo
echo "=== R4: make format_tg_match_header non-idempotent again ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="""        existing = str(match or '').strip()
        if existing.startswith('📌'):
            return existing
        clean = re.sub(r'^\\s*\\[[^\\]]{1,12}\\]\\s*', '', existing).strip() or existing"""
new="""        existing = str(match or '').strip()
        clean = existing"""
assert old in s, 'header anchor not found'
s=s.replace(old,new)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R4 header non-idempotent" tests/test_gate_teeth.py -k "match_header"
git checkout -- .

echo
echo "=== R5: re-append the stale wdl_matrix copy ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="""            if extra_key == 'wdl_matrix_tg_lines' and any(
                '市短矩阵' in ln for ln in extra
            ):
                continue
"""
assert old in s, 'dedupe anchor not found'
s=s.replace(old,'')
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R5 duplicate 市短" tests/test_gate_teeth.py -k "two_short_matrices or repaired_pack"
git checkout -- .

echo
echo "=== R6: drop both post-repair re-render call sites ==="
$PY scripts/r6_remove_rerender.py
run "R6 no post-repair re-render" tests/test_gate_teeth.py -k "repaired_pack"
git checkout -- .

echo
echo "=== R7: drop the partial-jc fallback keys (D8) ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="""                _fallback_keys = {
                    key for key in _jc_keys
                    if key not in _jc_vec and _norm_score(key) in {
                        _norm_score(k) for k in (extra_odds or {})
                    }
                }
                if _fallback_keys:
                    _page_vec = _implied_vec({
                        **{k: v for k, v in (extra_odds or {}).items()
                           if _norm_score(k) not in (_jc_keys - _fallback_keys)},
                    })"""
assert old in s, 'D8 anchor not found'
s=s.replace(old,'')
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R7 no partial-jc fallback" tests/test_gate_teeth.py -k "partial_jc"
git checkout -- .

echo
echo "=== R8: drop OverflowError from the collector guard ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="""                    except (TypeError, ValueError, OverflowError):
                        continue
                    if f > 1 and math.isfinite(f):"""
new="""                    except (TypeError, ValueError):
                        continue
                    if f > 1 and math.isfinite(f):"""
assert old in s,'D11 anchor'
s=s.replace(old,new,1)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R8 no OverflowError guard" tests/test_gate_teeth.py -k "over_range_int"
git checkout -- .

echo
echo "=== R9: drop the finite check in the collector ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="if f > 1 and math.isfinite(f):"
new="if f > 1:"
assert old in s,'D12 anchor'
s=s.replace(old,new,1)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R9 inf shadows legal value" tests/test_gate_teeth.py -k "infinite_odds"
git checkout -- .

echo
echo "=== R10: inf in one source must not win over a legal value in another ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="                    if f > 1 and math.isfinite(f):"
new="                    if f > 1:"
assert old in s,'D12 anchor'
s=s.replace(old,new,1)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R10 emit sees inf shadowing a legal copy" tests/test_gate_teeth.py -k "emit_uses_both or infinite_odds"
git checkout -- .

echo
echo "=== R11: let a long-shot share round to zero (D13) ==="
$PY - <<'PYEOF'
p='blade_deepseek_pipeline.py'
s=open(p,encoding='utf-8').read()
old="""                share = value if value > 0 and round(value, 4) > 0 else (0.0001 if value > 0 else 0.0)
                row['market'] = round(share, 4)"""
new="""                row['market'] = round(value, 4)"""
assert old in s,'D13 anchor'
s=s.replace(old,new,1)
open(p,'w',encoding='utf-8').write(s)
PYEOF
run "R11 longshot rounds to zero" tests/test_gate_teeth.py -k "longshot"
git checkout -- .

echo
echo "=== RESULT ==="
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: tree not clean after reverts:"; git status --short
  fail=1
fi
if [ $fail -eq 0 ]; then echo "ALL REVERTS GO RED — every fix has a guarding test."; else echo "SOME REVERTS STAYED GREEN — those tests have no teeth."; fi
exit $fail
