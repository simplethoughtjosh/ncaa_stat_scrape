# Versioning

The Captivate controller shows a version on the panel (for example **v1.1.001**). That number is how operators and engineers know which build is installed.

## Format

```text
Major.Minor.Patch
  1   . 1  . 001
```

| Part | Example | When it changes |
|------|---------|-----------------|
| **Major** | `1` | Only on a **GitHub commit** that is a large / breaking release. Commit message must explain the change. |
| **Minor** | `1` | Only on a **GitHub commit** that adds a meaningful set of features. Commit message must explain what is new since the last commit. |
| **Patch** | `001` | During ongoing work — new features, fixes, or polish **before** (or between) GitHub commits. Increment by 1 each time (`001` → `002` → …). Zero-padded to **3 digits**. |

**Current version: `1.1.029`**

## Rules (short)

1. **Day-to-day development / fixes** → bump **Patch** only (`1.1.001` → `1.1.002`).
2. **Commit to GitHub** that ships a batch of changes → bump **Minor** (or **Major** if breaking), reset or continue Patch as appropriate, and write a **commit message** that lists new features and fixes since the last commit.
3. Never bump Major/Minor casually while iterating locally without that GitHub commit + notes.

Suggested patterns when committing:

- Minor feature release: `1.1.014` → commit as `1.2.000` (or `1.2.001`) with notes.
- Patch-only hotfix commit: keep Major.Minor, bump Patch, still describe the fix in the commit message.

## Files that must stay in sync

Update **all** of these together when the version changes:

| File | What to set |
|------|-------------|
| `captivate_controller/STP_NCAA_Basketball/VERSION` | Plain text, e.g. `1.1.001` |
| `…/STP_NCAA_BasketballMain.html` | Visible badge `v1.1.001` and `CONTROLLER_VERSION` |
| `…/STP_NCAA_BasketballMain.xml` | `version="1.1.001"` attribute |

Automated tests check that these three agree.

## After changing the version

Run `scripts/deploy_controller.sh` (the agent does this automatically after controller edits). That rsyncs into `/Users/Shared/NewBlue/LiveEngine/DataControllers/STP_NCAA_Basketball`. Restart Captivate (or reopen the Data Controller) so the panel shows the new number.
