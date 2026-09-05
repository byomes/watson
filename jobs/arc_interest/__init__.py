"""jobs/arc_interest — lightweight "notify me for the next ARC team" waitlist.

Deliberately separate from jobs/arc/api.py: this captures name + email only,
applies a Kit tag, and stores a dedup row. It never creates login
credentials or an arc_readers row.
"""
