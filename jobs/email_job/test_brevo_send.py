"""jobs/email_job/test_brevo_send.py — Manual one-off test for brevo_send.py.

Not wired into cron or any job. Run manually once DNS/SPF/DKIM for
williamckyomes.com is confirmed in Brevo:

    PYTHONPATH=/home/billyomes/watson python3 jobs/email_job/test_brevo_send.py
"""
from jobs.email_job.brevo_send import send_email

if __name__ == "__main__":
    result = send_email(
        to_email="bill.yomes@gmail.com",
        to_name="Bill Yomes",
        subject="[Watson Brevo Test] Groundwork send",
        text_body="This is a manual test of the new Brevo transactional email helper (jobs/email_job/brevo_send.py). No existing Gmail SMTP job triggered this.",
        tags=["watson-brevo-test"],
    )
    print(result)
