# Feed fixtures

Fetched from NASCAR-hosted public JSON endpoints on October 11, 2026.

- `race_list_basic.json`: https://cf.nascar.com/cacher/2024/race_list_basic.json
  Only Cup series and fields used for event selection retained; all 41 events remain.
- `weekend-feed.json`: https://cf.nascar.com/cacher/2024/1/5392/weekend-feed.json
  First three result entries retained in each race/run.
- `lap-times.json`: https://cf.nascar.com/cacher/2024/1/5392/lap-times.json
  First three cars and lap zero through lap eight retained; source flags preserved.
- `live-feed.json`: https://cf.nascar.com/live/feeds/live-feed.json
  First three cars retained. Snapshot race ID 5629, timestamp
  `2026-10-11T16:27:25.359506-04:00`.

Values are source excerpts, not generated racing data. The repository's code
license does not extend to NASCAR data.
