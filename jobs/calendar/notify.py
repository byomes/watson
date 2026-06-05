from jobs.email_job.gmail import send_as_watson

_SIGNATURE = (
    "\n\nWatson\n"
    "AI-powered digital assistant\n"
    "Office of Dr. Bill Yomes\n"
    "williamckyomes.com/start"
)


def notify_reschedule(name: str, email: str, old_time: str, new_time: str) -> None:
    subject = "Your Appointment with Pastor Bill Has Been Rescheduled"
    body = (
        f"Hi {name},\n\n"
        f"Your appointment with Pastor Bill has been rescheduled.\n\n"
        f"Original time: {old_time}\n"
        f"New time: {new_time}\n\n"
        f"If you have questions, reply to this email."
        f"{_SIGNATURE}"
    )
    send_as_watson(email, subject, body)


def notify_cancellation(name: str, email: str, event_time: str) -> None:
    subject = "Your Appointment with Pastor Bill Has Been Cancelled"
    body = (
        f"Hi {name},\n\n"
        f"Your appointment with Pastor Bill scheduled for {event_time} has been cancelled.\n\n"
        f"To reschedule, reply to this email."
        f"{_SIGNATURE}"
    )
    send_as_watson(email, subject, body)
