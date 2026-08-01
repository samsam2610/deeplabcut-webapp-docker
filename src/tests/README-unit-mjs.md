# Pure-JS unit tests (.mjs)

Node here is **v16**, whose test runner finds nothing when pointed at a
directory (`node --test tests/unit/` reports `# tests 0`) and collapses a named
file into a single TAP line. Run each file directly instead — that prints one
`ok N - <name>` per assertion:

    cd deeplabcut-webapp-docker/src
    for f in tests/unit/*.mjs; do printf "%-38s " "$(basename $f)"; \
      node "$f" 2>&1 | grep -E "^# (pass|fail)" | tr '\n' ' '; echo; done

These cover pure, DOM-free modules under `static/js/components/`. Anything
needing a DOM is guarded by a source-assertion test in `tests/test_*_source.py`
instead — there is no DOM test runner in this project.
