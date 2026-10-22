# Enhancement tracker

Track unfinished features one at a time through **Backlog → In progress → Awaiting acceptance**.
After user acceptance, remove the completed feature from this tracker. Keep remaining feature IDs unchanged.
Read this tracker before starting feature work and update it as work progresses.
The user selects each feature; a suggestion does not authorize starting it.

## Feature backlog

No unfinished features.

## One-feature workflow

1. The user selects a feature. Agree on its specific behavior and acceptance checks before implementation, and record them below.
2. Mark only that feature **In progress** and implement it.
3. Run appropriate automated tests, verify the GUI where applicable, and rebuild the existing executable at **`dist/CastToTV.exe`** using `build.ps1`. Record the test results and build outcome. Do not create alternate build folders or executables. If rebuilding fails, report the cause, fix recoverable issues, and retry within the existing build directories. Request user input only when a required action needs it.
4. Mark the feature **Awaiting acceptance**, provide the reviewable build and manual checks, and end the turn. Passing tests alone does not constitute user acceptance.
5. If the user requests adjustments, revise the same feature, retest, and await acceptance again.
6. After explicit user acceptance, remove the completed feature's backlog row and detail section. Commit the feature and tracker updates together, recording the acceptance date and validation summary in the commit message, and report the commit hash. Include only relevant changes in the commit.
7. End the feature session after the commit. Suggest one logical next feature if helpful, then wait for the user's selection before starting more work.

Preserve the existing casting modes and saved checkbox preferences as features are added.

Current feature: None. Awaiting the user's next selection.
