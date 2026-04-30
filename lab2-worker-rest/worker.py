import os
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
import requests

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

client = MongoClient(os.environ["MONGODB_URI"])
db = client.get_default_database()

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 5))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")
MZINGA_URL = os.getenv("MZINGA_URL")
MZINGA_EMAIL = os.getenv("MZINGA_EMAIL")
MZINGA_PASSWORD = os.getenv("MZINGA_PASSWORD")


def slate_to_html(nodes: list) -> str:
	"""Minimal Slate AST → HTML serialiser."""
	html = ""
	for node in nodes or []:
		if node.get("type") == "paragraph":
			html += f"<p>{slate_to_html(node.get('children', []))}</p>"
		elif node.get("type") == "h1":
			html += f"<h1>{slate_to_html(node.get('children', []))}</h1>"
		elif node.get("type") == "h2":
			html += f"<h2>{slate_to_html(node.get('children', []))}</h2>"
		elif node.get("type") == "ul":
			html += f"<ul>{slate_to_html(node.get('children', []))}</ul>"
		elif node.get("type") == "li":
			html += f"<li>{slate_to_html(node.get('children', []))}</li>"
		elif node.get("type") == "link":
			url = node.get("url", "#")
			html += f'<a href="{url}">{slate_to_html(node.get("children", []))}</a>'
		elif "text" in node:
			text = node["text"]
			if node.get("bold"):
				text = f"<strong>{text}</strong>"
			if node.get("italic"):
				text = f"<em>{text}</em>"
			html += text
		else:
			html += slate_to_html(node.get("children", []))
	return html


def resolve_emails(recipients_list: list) -> list[str]:
#     """Resolve Payload relationship references to email addresses."""
#     if not relationship_list:
#         return []
#     ids = [ObjectId(r["value"]) for r in relationship_list if r.get("value")]
#     users = db.users.find({"_id": {"$in": ids}}, {"email": 1})
#     return [u["email"] for u in users if u.get("email")]
	return [recipient["value"]["email"] for recipient in recipients_list]


def send_email(to_addresses: list[str], subject: str, html: str,
			   cc_addresses: list[str] = None, bcc_addresses: list[str] = None):
	msg = MIMEMultipart("alternative")
	msg["Subject"] = subject
	msg["From"] = EMAIL_FROM
	msg["To"] = ", ".join(to_addresses)
	if cc_addresses:
		msg["Cc"] = ", ".join(cc_addresses)
	msg.attach(MIMEText(html, "html"))
	all_recipients = to_addresses + (cc_addresses or []) + (bcc_addresses or [])
	with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
		server.sendmail(EMAIL_FROM, all_recipients, msg.as_string())


def process(doc: dict, token: str):
	doc_id = doc["id"]
	log.info(f"Processing communication {doc_id}")
	# db.communications.update_one({"_id": doc_id}, {"$set": {"status": "processing"}})
	update_comm_status(token, doc_id, "processing")
	try:
		to_emails = resolve_emails(doc.get("tos") or [])
		if not to_emails:
			raise ValueError("No valid 'to' email addresses found")
		cc_emails = resolve_emails(doc.get("ccs") or [])
		bcc_emails = resolve_emails(doc.get("bccs") or [])
		html = slate_to_html(doc.get("body") or [])
		send_email(to_emails, doc["subject"], html, cc_emails, bcc_emails)
		# db.communications.update_one({"_id": doc_id}, {"$set": {"status": "sent"}})
		update_comm_status(token, doc_id, "sent")
		log.info(f"Communication {doc_id} sent successfully")
	except Exception as e:
		log.error(f"Failed to process communication {doc_id}: {e}")
		# db.communications.update_one({"_id": doc_id}, {"$set": {"status": "failed"}})
		update_comm_status(token, doc_id, "failed")


def get_auth_token():
	print("Login json:", {"email": MZINGA_EMAIL, "password": MZINGA_PASSWORD})
	print("Login url:", f"{MZINGA_URL}/api/users/login")

	resp = requests.post(
		f"{MZINGA_URL}/api/users/login",
		json={"email": MZINGA_EMAIL, "password": MZINGA_PASSWORD}
	)
	
	resp.raise_for_status()
	token = resp.json()["token"]
	log.info("Auth token:", token)
	return token


def get_auth_headers(token):
	return {"Authorization": f"Bearer {token}"}


def update_comm_status(token, comm_id, status):
	resp = requests.patch(
		f"{MZINGA_URL}/api/communications/{comm_id}",
		json={"status": status},
		headers=get_auth_headers(token)
	)
	resp.raise_for_status()


def get_pending_comms(token):
	resp = requests.get(
		f"{MZINGA_URL}/api/communications",
		params={"where[status][equals]": "pending", "depth": 1},
		headers=get_auth_headers(token)
	)
	resp.raise_for_status()
	return resp.json().get("docs", [])


def poll(token):
	log.info(f"Worker started. Polling every {POLL_INTERVAL}s")
	while True:
		# doc = db.communications.find_one({"status": "pending"})
		docs = get_pending_comms(token)
		if docs:
			for doc in docs:
				process(doc, token)
		else:
			time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
	token = get_auth_token()
	poll(token)