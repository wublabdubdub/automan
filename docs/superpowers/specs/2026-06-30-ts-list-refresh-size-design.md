# TS List Refresh Size Design

## Goal

Add an explicit refresh mode for `./automan list -t ts` so the `Size` column can reflect the current database table size after mars3 compact.

## Design

The default list behavior stays unchanged: it reads the stored benchmark snapshot from `result.json` or `database/table-size.json` and does not connect to the database.

When the user passes `--refresh-size`, `list` reloads an inventory with real target credentials, queries the current target table size for each TS result whose target can be matched, updates the in-memory row, and writes the refreshed size back to the run's `result.json` and `database/table-size.json`. The command accepts `-i/--inventory`; when omitted, it tries `automan.yml` in the current directory.

## Error Handling

Refresh mode is TS-only for result rows, but the inventory may be any benchmark inventory that contains the same target IDs with real credentials. If the inventory cannot be loaded, has no benchmark targets, or a run cannot be matched to a target/table, the command keeps the old snapshot for that run. Database query failures also keep the old snapshot and store the refresh error in memory only.

## Testing

Unit tests cover default snapshot behavior and refresh behavior with a fake runner. CLI contract tests verify the new flags are wired through.
