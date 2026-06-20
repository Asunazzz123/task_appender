# Notification Reminders Design

**Date:** 2026-06-20

**Status:** Approved for implementation planning

## Goal

Add self-contained macOS task reminders to `task_appender`. The existing Web UI edits reminder rules and notification settings, while the reminder worker runs only for the lifetime of `./start_ui.sh`. The merged implementation must not depend on the external `Notification` repository.

## Product Decisions

- Remind both daily tasks and tasks with `due_at`.
- Daily tasks continue to use `recurrence.time` and produce one reminder per day.
- A due task may define multiple task-specific reminder rules such as one day before at 09:00 and on the due date at 09:00.
- Missed reminders are eligible for catch-up for 120 minutes by default. Older reminders are skipped.
- `todo`, `doing`, and `blocked` tasks are active. `done` and `archived` tasks never produce reminders.
- `start_ui.sh` owns the reminder worker lifecycle. Closing the UI stops reminder checks.
- Do not add `launchd`, systemd-like installation, login-start behavior, or a scheduler installation lifecycle.
- Include the native notification sender core in this repository. Do not invoke the external `Notification/bin/notify`.
- The `merge-notification` branch includes the current `.gitignore` additions for `data/`, `exports/`, and `.superpowers/`, but excludes the user's current task-data and export changes.

## Non-Goals

- Reminders while `start_ui.sh` is not running.
- Multiple daily reminders for one daily task in the first version.
- Email, mobile push, remote notification services, or cross-platform notification backends.
- Forcing notification placement, bypassing Focus, silently granting notification permission, or claiming that a submitted notification was visibly displayed.
- A complete notification setup/install/status/uninstall CLI lifecycle.

## Architecture

The Web UI and CLI write task reminder rules to the task store. Notification-wide preferences live in a settings file beside the task store. When `taskmgr serve` starts, it starts a reminder worker in the same process. The worker periodically loads a consistent snapshot of the task store and settings, calculates eligible occurrences, filters them through a delivery ledger, and submits new occurrences to the bundled native notifier.

The responsibilities are divided as follows:

- `taskmgr/reminders.py`: reminder-rule validation, rule parsing, occurrence calculation, active-status filtering, catch-up-window filtering, retry eligibility, delivery keys, and worker lifecycle.
- `taskmgr/notifier.py`: build and launch `Notification Agent.app`, submit UTF-8 notification requests, and report only verifiable process outcomes.
- `taskmgr/settings.py`: defaults, validation, and atomic persistence for notification settings and the delivery ledger.
- `taskmgr/server.py`: start and stop the reminder worker with the HTTP server, expose notification settings/setup/test endpoints, and accept reminder fields in task mutations.
- `taskmgr/model.py` and `taskmgr/graph.py`: preserve and validate reminder rules as part of the task model.
- `taskmgr/render.py`: edit reminders and settings in the Web UI and show reminder summaries in generated task views.
- `scripts/notification-agent.applescript`: bundled native macOS notification application source.

The worker only reads `tasks.yaml`. It never writes the task graph or exports. Task edits continue to use the existing atomic save and full-export regeneration path.

## Task Reminder Data

Due-task rules are stored on the task:

```yaml
- id: T-0002
  title: 期末周复习
  kind: short
  status: doing
  due_at: '2026-07-04'
  reminders:
    - days_before: 1
      time: '09:00'
    - days_before: 0
      time: '09:00'
```

`reminders: []` means that the due task has no due reminder. The field is present after normalization so task serialization is stable.

Each rule must satisfy all of these requirements:

- `days_before` is an integer from 0 through 3650.
- `time` is a valid 24-hour `HH:MM` value.
- A task cannot contain duplicate `(days_before, time)` pairs.
- A non-empty reminders list requires `due_at`.
- Daily tasks use `recurrence.time`; their `reminders` list must be empty in the first version.

Existing task files without `reminders` normalize to an empty list and remain valid.

## Notification Settings

Settings live beside the selected task database. For the default database, the path is `data/settings.yaml`. If the task database is `/path/to/tasks.yaml`, the settings path is `/path/to/settings.yaml`.

```yaml
version: 1
notifications:
  enabled: false
  timezone: Asia/Shanghai
  default_sound: Glass
  missed_grace_minutes: 120
  check_interval_seconds: 60
```

Defaults apply when the file does not exist. The initial global switch is off, preventing an unexpected notification before the user initializes and tests the native app. Validation requires:

- `enabled` is boolean.
- `timezone` resolves through the standard-library `zoneinfo` database.
- `default_sound` is a string; an empty string disables sound.
- `missed_grace_minutes` is between 0 and 1440.
- `check_interval_seconds` is between 15 and 3600.

Settings are local user state and do not trigger task export regeneration.

## Scheduling Semantics

For a due task with due date `D`, a rule `{days_before: N, time: HH:MM}` schedules one occurrence at local date `D - N days` and time `HH:MM` in the configured timezone.

For an active daily task, the worker schedules one occurrence for the current local date at `recurrence.time`.

On every scan at time `now`, the engine considers occurrences in the inclusive window:

```text
now - missed_grace_minutes <= scheduled_at <= now
```

The worker runs immediately when the server starts and then waits `check_interval_seconds` between scans. This gives short UI restarts catch-up behavior without creating a backlog after the UI has been closed for a long time.

An occurrence key contains the task ID, occurrence family (`daily` or `due`), relevant task date, rule values, and planned timestamp. Consequently:

- The same occurrence is delivered at most once after success.
- Changing a due date or reminder rule creates a new occurrence identity.
- Completing or archiving a task before a retry suppresses the retry.

## Delivery Ledger and Retry

The delivery ledger lives beside the task store as `reminder_state.json`. It is ignored by Git and written atomically. It stores successful deliveries and failed-attempt metadata; it does not modify `tasks.yaml`.

After a native submission succeeds, the engine records the occurrence as delivered. A backend failure leaves it undelivered and schedules retries after 1, 5, 15, and 30 minutes, capped at 30 minutes, while the occurrence remains inside the catch-up window. Old delivered and failed records are pruned after 90 days.

Only one scan executes at a time. A process-local lock prevents overlapping timer callbacks. Notification failures are logged and must never terminate the Web server.

## Native Notification Sender

The implementation ports the essential behavior of the external `Notification` project into Python and includes only the required AppleScript source.

The native app is generated at `build/Notification Agent.app` and uses bundle identifier `local.notification.agent`. Building it performs these steps without a shell:

1. Run `/usr/bin/osacompile` with an argument array.
2. Update the generated `Info.plist` using Python `plistlib`.
3. Run `/usr/bin/codesign --force --sign -` with an argument array.

Sending creates a private temporary request directory containing `title.txt`, `message.txt`, and optional `sound.txt`, then runs `/usr/bin/open -W -n -a <app> <request-dir>`. Notification values are data files, never shell or AppleScript interpolation.

Task notifications use title `任务提醒 · <task-id>`. The message contains the task title followed by either `每日 HH:MM` or `截止 YYYY-MM-DD（提前 N 天 HH:MM）`; a same-day rule is rendered as `截止当天 HH:MM`. The configured default sound applies to every task notification in the first version.

The sender reports successful request submission only when `open` returns zero. It does not report that the user saw a banner. macOS notification permission and Focus behavior remain under system control.

## `start_ui.sh` Lifecycle

`start_ui.sh` remains the only normal launch entry. It changes from the system `python3` to the required Conda environment:

```bash
exec conda run -n agent python -m taskmgr.cli serve --host "$HOST" --port "$PORT"
```

`taskmgr serve` creates the HTTP server and reminder worker, starts the worker before entering `serve_forever`, and stops and joins it in the server cleanup path. Ctrl-C therefore stops both components. No background process survives the UI process.

## Web UI and API

The existing task dialog gains a reminder-rule editor:

- It is enabled only when a due date is present.
- Each row edits `days_before` and `time`.
- Users can add and remove rows.
- Creating or updating the task sends the full `reminders` array.
- Validation errors return the existing structured API error response and are shown through the existing status area.

The task graph toolbar gains a notification-settings button. Its dialog contains:

- Global enable switch.
- Timezone.
- Default sound.
- Missed-reminder window.
- Check interval.
- Native app build state.
- “Initialize Notification App” and “Send Test Notification” actions.

The settings dialog explains that request submission does not guarantee visible display and that macOS permission or Focus can suppress a banner.

The server adds these local APIs:

- `GET /api/settings/notifications`
- `PUT /api/settings/notifications`
- `GET /api/notifications/status`
- `POST /api/notifications/setup`
- `POST /api/notifications/test`

Setup builds the app if needed and opens it without a notification request so macOS can establish the sender. Test submits a clearly labeled task_appender test notification. Setup and test reject non-loopback clients, even if the user started the Web server on a non-loopback host.

## CLI Task Editing

Repository policy requires a CLI path for task-data changes. The task CLI therefore gains:

- Repeatable `add --reminder Nd@HH:MM` options for a new due task.
- `reminders set --task REF --rule Nd@HH:MM [--rule ...]` to atomically replace a task's reminder list.
- `reminders clear --task REF` to remove all due reminders.

These commands use the existing mutation validation and full-export regeneration path. They do not add notification daemon installation commands.

## Export Behavior

Any task reminder edit is a task-model change and must regenerate all five dependent exports. Markdown and HTML task details show a compact reminder summary such as `提前 1 天 09:00；当天 09:00`. Mermaid, DOT, scoreboard, and HTML are still regenerated even when a specific view does not display reminder details.

Settings changes and delivery-ledger changes are not task-graph changes and do not regenerate exports.

## Error Handling

- Invalid task reminder data fails graph validation and is never scheduled.
- Invalid settings are rejected without overwriting the last valid file.
- A missing native app is an actionable “initialize Notification App” state, not a successful delivery.
- Build, signing, launch, and delivery errors preserve the underlying diagnostic text for logs and API responses.
- Worker exceptions are caught per scan and logged; the next timer interval tries again.
- A notification is recorded as delivered only after the native command returns success.

## Testing

All project Python tests and tooling run through `conda run -n agent`.

New and expanded tests cover:

- Model normalization and validation for absent, valid, duplicate, malformed, daily, and no-due reminder rules.
- CLI parsing and atomic replacement/clearing of reminder rules.
- Occurrence calculation for daily and due tasks, active statuses, timezone conversion, catch-up boundaries, and edited rules.
- Delivery-key stability, successful deduplication, retry backoff, completion-before-retry, ledger pruning, and atomic state writes.
- Worker immediate scan, periodic scan, clean shutdown, exception containment, and overlap prevention using a fake clock/notifier.
- Native app build and delivery using fake `osacompile`, `codesign`, and `open` executables, including UTF-8, shell metacharacters, missing values, and propagated backend failures.
- Settings defaults, validation, round-trip persistence, and path derivation for custom databases.
- Web task payloads, settings APIs, loopback restriction, setup/test actions, and rendered controls.
- Export reminder summaries and the required full-render workflow.

Native tests do not display real notifications or install system services.

## Documentation and Delivery

Update `README.md` and `USAGE.md` with the `start_ui.sh` reminder lifecycle, Web setup flow, reminder-rule CLI syntax, macOS permission caveats, and the explicit limitation that reminders stop when the UI stops.

Implementation occurs on the isolated worktree for branch `merge-notification`. The branch carries the approved `.gitignore` changes, source, tests, documentation, and clean-baseline exports required by repository policy. It does not include the user's uncommitted `data/tasks.yaml` or export changes from the original worktree.

Before opening the PR, run in order:

```bash
conda run -n agent python -m taskmgr.cli validate
conda run -n agent python -m taskmgr.cli render --format mermaid
conda run -n agent python -m taskmgr.cli render --format dot
conda run -n agent python -m taskmgr.cli render --format markdown
conda run -n agent python -m taskmgr.cli render --format html
conda run -n agent python -m taskmgr.cli render --format scoreboard
conda run -n agent python -m unittest discover -s tests
conda run -n agent python -m pytest
```
