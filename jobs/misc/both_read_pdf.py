import os
import sqlite3
from io import BytesIO
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams
from requests import post
from python_dotenv import load_dotenv

load_dotenv()

DATABASE = os.path.expanduser("~/watson/data/watson.db")
LOG_FILE = os.path.expanduser("~/watson/logs/pdf_skill.log")

def log_error(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{message}\n")

def extract_text_from_pdf(pdf_path):
    text = ""
    with open(pdf_path, 'rb') as file:
        with BytesIO(file.read()) as fp:
            extract_text_to_fp(fp, output_fp=text, laparams=LAParams())
    return text.strip()

def create_pdf(text, pdf_path):
    response = post("https://api.ollama.io/pdf", json={"text": text})
    if response.status_code == 200:
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        return True
    else:
        log_error(f"Failed to create PDF: {response.text}")
        return False

def db_connect():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    conn = db_connect()
    cursor = conn.cursor()

    # Example usage
    pdf_path = "example.pdf"
    text = extract_text_from_pdf(pdf_path)
    if text:
        new_pdf_path = "output.pdf"
        if create_pdf(text, new_pdf_path):
            print(f"PDF created: {new_pdf_path}")
        else:
            print("Failed to create PDF.")
    else:
        print("No text extracted from PDF.")

if __name__ == "__main__":
    main()