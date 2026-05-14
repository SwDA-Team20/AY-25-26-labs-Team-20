import os
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import requests
import structlog

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.requests import RequestsInstrumentor

load_dotenv()



# configure enviroment constants
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 5))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")
MZINGA_URL = os.getenv("MZINGA_URL")
MZINGA_EMAIL = os.getenv("MZINGA_EMAIL")
MZINGA_PASSWORD = os.getenv("MZINGA_PASSWORD")
SERVICE_NAME_VALUE = os.getenv("OTEL_SERVICE_NAME", "email-worker")
OTLP_EXPORTER_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

# configure open telemetry for tracing
resource = Resource(attributes={
    SERVICE_NAME: SERVICE_NAME_VALUE,
    SERVICE_VERSION: "1.0.0",
})

tracer_provider = TracerProvider(resource=resource)
otlp_exporter = OTLPSpanExporter(endpoint=f"{OTLP_EXPORTER_ENDPOINT}")
tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
trace.set_tracer_provider(tracer_provider)

RequestsInstrumentor().instrument()
tracer = trace.get_tracer(SERVICE_NAME_VALUE)

# configure logging

# old logging (not structured)
# logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# log = logging.getLogger(__name__)

# json logging

def add_otel_context(logger, method, event_dict):
    """Inject active trace_id and span_id into every log entry."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_otel_context, # add context from current open telemetry trace and span
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service=SERVICE_NAME_VALUE)


# communication handling
def slate_to_html(nodes: list) -> str:
	"""Minimal Slate AST → HTML serialiser."""
	# span wrapping the entire method call, with nodes count as attributes
	with tracer.start_as_current_span("serialize_body") as span:
		span.set_attribute("node_count", len(nodes))

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
	'''Resolve Payload relationship references to email addresses'''
	# when using depth=1 in the mzinga api request, the recipients are already resolved
	# and can be found inside the json at value.email
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
	# wrap smtp send with a send_email span (# of recipients is an attribute)
	with tracer.start_as_current_span("send_email") as span:
		span.set_attribute("recipient_count", len(all_recipients))
		with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
			server.sendmail(EMAIL_FROM, all_recipients, msg.as_string())


def process(doc: dict, token: str):
	'''Tries to process the given communication. The stats is immediatly updated to "processing", then
	after resolving all recipients, the communication is sent and its status is updated to "sent". If any step
	fails, the communication status is set to "failed"'''

	with tracer.start_as_current_span("process_communication") as span:
		doc_id = doc["id"]
		span.set_attribute("doc_id", doc_id)

		# adds doc id to next logs
		structlog.contextvars.bind_contextvars(doc_id=doc_id)

		# actual process of communication
		update_comm_status(token, doc_id, "processing")
		try:
			to_emails = resolve_emails(doc.get("tos") or [])
			if not to_emails:
				raise ValueError("No valid 'to' email addresses found")
			cc_emails = resolve_emails(doc.get("ccs") or [])
			bcc_emails = resolve_emails(doc.get("bccs") or [])
			html = slate_to_html(doc.get("body") or [])
			send_email(to_emails, doc["subject"], html, cc_emails, bcc_emails)
			update_comm_status(token, doc_id, "sent")
			log.info(f"Communication {doc_id} sent successfully")
			log.info(f"Communication {doc_id} sent successfully")
		except Exception as e:
			log.error(f"Failed to process communication {doc_id}: {e}")
			update_comm_status(token, doc_id, "failed")
		finally:
			# remove doc id from next logs
			structlog.contextvars.unbind_contextvars("doc_id")




def get_auth_token():
	'''Authenticate to the login api with the admin credentials to get the authentication token'''

	resp = requests.post(
		f"{MZINGA_URL}/api/users/login",
		json={"email": MZINGA_EMAIL, "password": MZINGA_PASSWORD}
	)
	
	resp.raise_for_status()
	token = resp.json()["token"]
	log.info(f"Auth token: {token}")
	return token


def get_auth_headers(token):
	'''Helper function to generate header in the correct format to hold authentication token'''
	return {"Authorization": f"Bearer {token}"}


def update_comm_status(token, comm_id, status):
	'''Updates the given communication status with the provided one (requires authentication token)'''
	resp = requests.patch(
		f"{MZINGA_URL}/api/communications/{comm_id}",
		json={"status": status},
		headers=get_auth_headers(token)
	)
	resp.raise_for_status()


def get_pending_comms(token):
	'''Fetches all the communications that have a status set to "pending"'''
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
		docs = get_pending_comms(token)
		if docs:
			for doc in docs:
				process(doc, token)
		else:
			time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
	token = get_auth_token()
	poll(token)