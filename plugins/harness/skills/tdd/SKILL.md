---
name: tdd
description: Develop a behavior through a failing test, the smallest change, and cleanup. Load for a feature with observable acceptance criteria or a bug with a reproducible failure.
---

# Test first

Start with one behavior the user can observe. Describe its input, expected
result, and the check that will prove it. Prefer an existing test runner and
the smallest realistic fixture.

1. Write a focused test before changing production code. Prepare the input,
   exercise the behavior, then check the result.
2. Run that test. It must fail because the behavior is missing or wrong. Fix
   test setup or syntax errors before treating a failure as useful evidence.
   If it already passes, choose a case that exposes the requested change.
3. Make the smallest production change that passes the test.
4. Run the focused test and related tests. If they fail, correct the behavior;
   do not weaken assertions, skip tests, or remove coverage to obtain a pass.
5. Clean up the implementation while tests remain green. Keep unrelated
   refactors out of this cycle.

Use real local components where practical. Replace external services, clock
access, or randomness at their boundaries when needed for a safe, repeatable
test. Never spend money or send live user actions from a test.

Preserve existing tests unless the requested behavior intentionally changes
their contract. Explain any such change and retain meaningful regression
coverage. A passing check is evidence only for the behavior it exercises.

Return: behavior tested, failing command and reason, implementation changed,
passing commands and results, and any remaining gap.
