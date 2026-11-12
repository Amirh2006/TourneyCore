# Note: Git Branching Strategy

## Strategy Overview

Following the user's requirements, the Git branching strategy for TourneyCore is:

### 1. Branch Structure
- **`main`** branch: Production-ready state, updated at the end of each Phase
- **`develop`** branch: Integration branch where all features are merged
- **Feature branches** (`feature/phase-1`, `feature/phase-2`, etc.): Created from `develop` for each phase

### 2. Workflow for Each Phase

#### Phase Start:
1. Check out `develop` branch
2. Create new feature branch: `git checkout -b feature/phase-N develop`

#### During Phase:
- All code changes, commits, and commits made on the feature branch
- Commit messages follow conventional commit style (feat:, fix:, refactor:, chore:, docs:)
- No merging to develop until phase completion

#### Phase Completion:
1. Stop coding
2. Generate Persian Markdown report in `PHASE_N_REPORT.md`
3. Commit the report to the feature branch
4. Merge feature branch into `develop`: `git checkout develop && git merge feature/phase-N`
5. Delete the feature branch: `git branch -d feature/phase-N`
6. Merge `develop` into `main` (since phase is complete): `git checkout main && git merge develop`
7. Push all changes to remote

### 3. Current State (After Phase 1)

```
$ git branch -a
  develop
* main
  remotes/origin/develop
  remotes/origin/main
```

Both `develop` and `main` have the same content (Phase 1 complete):
- 4 commits total, both branches synchronized at `0c21c22`
- All scaffold infrastructure in place
- Persian report and this strategy note committed
- `main` verified as the **default branch** on GitHub (`gh repo edit --default-branch main`)

> **Note:** Phase 1 was committed directly on `develop` before this branching
> strategy was introduced. Starting with Phase 2, every phase is developed on its
> own `feature/phase-N` branch as described above.

### 4. Next Phase (Phase 2) Execution

When starting Phase 2:
1. Create feature branch: `git checkout -b feature/phase-2 develop`
2. Implement Core Data Models (`core/models.py`)
3. After completion:
   - Merge feature/phase-2 into develop
   - Delete feature/phase-2
   - Merge develop into main
   - Push all changes

### 5. Remote Repository

- **Origin:** https://github.com/Amirh2006/TourneyCore.git
- **Default branch on remote:** main (set up)
- **Tracking:** Both develop and main track their remote counterparts

### 6. Conventional Commits (Enforced)

All commit messages must follow this format:
- `feat: ` - New feature
- `fix: ` - Bug fix
- `refactor: ` - Code restructuring
- `chore: ` - Routine maintenance
- `docs: ` - Documentation updates
- `style: ` - Formatting, missing semicolons, etc.
- `test: ` - Adding missing tests
- `perf: ` - Performance improvement

Example: `feat: add pydantic models for Player and Match`

---

**Current Branch:** `develop` (ready for Phase 2 feature branch creation)
**Main Branch:** Up to date with Phase 1 completion