# Notification Trigger Cron Schedule

For U.S. regular market hours (9:30 AM to 4:00 PM Eastern Time),
starting 1 hour before open and ending 1 hour after close, run every 30 minutes between:

- **8:30 AM ET** and **5:00 PM ET**

Use this cron expression:

```cron
30 8-16 * * 1-5
0 9-17 * * 1-5
```

Equivalent run times (ET):
8:30, 9:00, 9:30, 10:00, 10:30, 11:00, 11:30, 12:00, 12:30, 1:00, 1:30, 2:00, 2:30, 3:00, 3:30, 4:00, 4:30, 5:00.

> Ensure your scheduler is configured to `America/New_York` to track DST automatically.
