# Multi-NAS Share-Level Permission Sync Design

## Status

Approved design for a first implementation pass. The user confirmed that NAS
permissions are scoped at the shared-folder level, not at arbitrary child
directories.

## Goal

Support multiple NAS servers, each with an administrator-managed indexing
account, and keep a local database of file and directory names synchronized on
a schedule. End users authenticate with their own NAS account at query time.
The application uses the NAS API to determine which shared folders that user
can access, then serves search and browse results from the local database
without re-scanning the NAS for each user.

## Non-Goals

- Do not implement child-directory ACL filtering in the first pass.
- Do not depend on QNAP event callbacks as the source of truth.
- Do not store end-user NAS passwords.
- Do not expose write, delete, download, upload, or file mutation operations.

## Current Context

The current application has one `nas_config` row constrained to `id = 1`.
`entries.full_path` is globally unique, and scans are generation-based full
traversals: a successful scan writes a new generation and deletes entries from
older generations. The QNAP adapter already logs in through `authLogin.cgi`
and reads File Station `get_tree` and `get_list` data.

This design keeps the existing read-only local-index model but changes the
ownership boundaries:

- Admin indexing credentials synchronize data into the database.
- End-user credentials are used only to discover accessible shared folders.
- Query-time authorization is enforced by filtering local rows by NAS and
  shared-folder root.

## QNAP Change Event Assessment

QNAP File Station APIs provide listing and access-related endpoints, but there
is no clear official File Station webhook or durable file-change stream that
can be treated as a reliable index source. QNAP Notification Center and Qmiix
can emit or route NAS events, but they are better treated as optional
accelerators.

The first implementation should use scheduled incremental synchronization as
the authoritative path. If an event channel is later configured, an incoming
event should mark a NAS or share as due for sync sooner; the scheduled sync
still remains the correctness backstop.

## Data Model

### `nas_servers`

Stores one row per NAS:

- `id`
- `name`
- `base_url`
- `port`
- `use_https`
- `enabled`
- `sync_interval_minutes`
- `full_resync_interval_hours`
- `created_at`
- `updated_at`

The endpoint is derived from `base_url`, `port`, and `use_https`.

### `nas_credentials`

Stores the indexing account for a NAS:

- `nas_id`
- `username`
- `password`
- `updated_at`

The first pass can keep the existing local SQLite plaintext behavior, but the
UI and README must continue to state that this is intended only for a trusted
local machine. A later hardening pass can move these secrets into the macOS
Keychain or another local secret store.

### `entries`

Add NAS ownership and shared-folder root:

- `nas_id`
- `share_path`
- existing file metadata fields

Change uniqueness from `full_path` to `(nas_id, full_path)`.

Add indexes for:

- `(nas_id, share_path)`
- `(nas_id, parent_path)`
- `(nas_id, entry_type)`
- `(nas_id, scan_generation)`

The FTS table must be rebuilt or recreated so search results join back to
`entries` and always apply NAS and share filters.

### `share_sync_state`

Tracks scheduling and health per NAS share:

- `nas_id`
- `share_path`
- `last_synced_at`
- `last_full_synced_at`
- `next_sync_at`
- `last_generation`
- `status`
- `last_error`

This allows one failing share to be reported clearly and retried without
making other NAS servers look unhealthy.

### `sync_runs`

Replaces or extends `scan_runs` with NAS-aware runs:

- `id`
- `nas_id`
- `scope` (`nas`, `share`, or `directory`)
- `share_path`
- `generation`
- `status`
- `started_at`
- `finished_at`
- `processed_entries`
- `current_path`
- `error_summary`

The existing `scan_errors` table can point to `sync_runs` instead of the old
single-NAS scan concept, or a new `sync_errors` table can be introduced during
migration.

## Synchronization Design

### Scheduler

Run an in-process scheduler as part of the FastAPI lifespan. It periodically
selects due `share_sync_state` rows for enabled NAS servers and starts sync
jobs with a per-NAS lock. This keeps the first implementation simple and
consistent with the current in-process background scan manager.

The scheduler should avoid starting duplicate work:

- One active sync per NAS.
- A manual sync request can mark a NAS or share as due immediately.
- A full resync can be scheduled independently from normal incremental sync.

### Initial Sync

For each enabled NAS:

1. Log in with the indexing account.
2. Read accessible shared folders.
3. Create or update `share_sync_state` rows for each share.
4. Traverse each share and upsert entries with `nas_id` and `share_path`.
5. Mark shares that disappear from the indexing account as inactive or remove
   their entries after a confirmed successful sync.

### Incremental Sync

Because shared-folder-level permissions are sufficient, incremental sync can be
directory based rather than ACL based.

For each due share:

1. Traverse the share using File Station listings.
2. Upsert observed children.
3. For each successfully listed directory, delete local direct children that
   were not observed in that directory listing.
4. Update `share_sync_state` and `sync_runs` progress.

This handles new, renamed, modified, and deleted files without requiring a
global full-table stale-generation delete. A periodic full resync still runs to
protect against missed directories, interrupted jobs, and unexpected NAS API
behavior.

### Failure Behavior

If a directory or share fails:

- Keep existing local entries for that scope.
- Mark the run failed with the current path and safe error summary.
- Schedule a retry using the normal interval or a shorter bounded retry delay.
- Do not delete old rows for a failed scope.

This preserves the existing safety rule: stale data is better than accidental
data loss when a sync cannot complete.

## End-User Authorization Flow

End users do not create app-local accounts in the first pass. They choose a NAS
and enter their NAS username and password when they need to browse or search.

Flow:

1. User selects a NAS.
2. User enters NAS credentials.
3. Backend logs in to that NAS.
4. Backend calls the share listing API.
5. Backend stores only a short-lived session record containing:
   - `nas_id`
   - username
   - accessible `share_path` values
   - expiry time
6. Browse and search queries filter by:
   - selected `nas_id`
   - `share_path IN accessible_shares`

User passwords must not be written to SQLite. If a persistent browser session
is needed, store only an opaque signed session id or secure cookie that maps to
the short-lived permission cache.

## Query Behavior

The dashboard, browse page, and search page become NAS-aware.

Browse:

- The root view lists only accessible shares for the selected NAS.
- Expanding or opening a directory queries `entries` by `(nas_id, parent_path)`.
- Results are never fetched from the NAS during normal browse.

Search:

- Search applies FTS or fallback matching locally.
- Every query includes `nas_id` and accessible `share_path` filters.
- Results can display the NAS name and share name when multiple NAS servers
  are visible in the UI.

## Admin UI

Replace the single settings page with an admin NAS management area:

- List configured NAS servers.
- Add or edit NAS connection details.
- Save indexing credentials.
- Test indexing-account connection.
- Show visible shares for the indexing account.
- Configure sync interval and enabled state.
- Trigger manual sync for a NAS or share.
- Show last sync status, processed count, and error summary.

The first pass can keep this as a local trusted admin interface with no
separate app admin login, matching the current local-only design.

## User UI

Add a simple user access flow:

- Select NAS server.
- Enter NAS username and password.
- Test access and cache visible shares for the session.
- Browse and search only after a successful access check.

The existing `/settings` route can become admin-only navigation, while the
normal first screen should guide users to select NAS access before browsing.

## Migration

Use an application-managed migration step because the project currently relies
on `Base.metadata.create_all()` rather than Alembic.

Migration steps:

1. Create new NAS tables.
2. If the existing single `nas_config` row exists, migrate it to one
   `nas_servers` row and one `nas_credentials` row.
3. Add `nas_id` and `share_path` to `entries`.
4. Backfill `nas_id` from the migrated NAS row.
5. Backfill `share_path` from the first path segment of `full_path`.
6. Recreate affected unique indexes and FTS triggers.
7. Preserve existing entries where possible so users do not need an immediate
   full rescan.

If SQLite schema changes become too complex, the implementation can create a
new database file after warning the user. The preferred path is an in-place
migration.

## Testing

Unit tests:

- NAS repository CRUD and migrated single-NAS config.
- Entry upsert and uniqueness scoped by NAS.
- Share-path extraction from full paths.
- Permission filtering for browse and search.
- Scheduler due-row selection and per-NAS locking.
- Failure behavior keeps old entries.

Integration tests:

- Multiple NAS servers with identical paths do not collide.
- A user with access to one share cannot see another share.
- Search results honor NAS and share filters.
- Manual sync updates one NAS without touching another.
- Failed sync does not delete existing entries.

Manual checks:

- Add two NAS servers.
- Save indexing credentials and list shares.
- Log in as a user with access to one shared folder.
- Confirm browse and search show only that shared folder.
- Add, rename, and delete files on NAS, then confirm scheduled sync updates
  local results.

## Rollout Plan

Implement in phases:

1. Multi-NAS schema and repositories.
2. NAS-aware QNAP client usage and entry storage.
3. User share-permission check and query filtering.
4. Scheduler and incremental sync state.
5. Admin and user UI updates.
6. Migration polish and documentation.

This order keeps authorization filtering available before exposing broader
multi-NAS search in the UI.
