"""
Watson Facebook Scheduler
Checks the facebook_queue for due posts and fires them.
Add to cron: */15 * * * * python3 ~/watson/jobs/facebook/scheduler.py
"""
from facebook_post import init_db, run_due_posts

if __name__ == "__main__":
    init_db()
    run_due_posts()
