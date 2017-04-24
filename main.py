import os
import random
import string

import dateutil.parser
from apiclient.discovery import build
from flask import Flask, request, render_template, session, redirect
from google.appengine.api import users
from google.appengine.api.modules import modules
from oauth2client.client import HttpAccessTokenRefreshError
from oauth2client.contrib.flask_util import UserOAuth2

import settings
from myapp.calendar import *

app = Flask(__name__)

app.config['GOOGLE_OAUTH2_CLIENT_ID'] = settings.CLIENT_ID
app.config['GOOGLE_OAUTH2_CLIENT_SECRET'] = settings.CLIENT_SECRET

app.secret_key = settings.SECRET_KEY

oauth2 = UserOAuth2(app, scopes=settings.SCOPE)

def generate_csrf_token():
    if '_csrf_token' not in session:
        random_string = ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(32))
        session['_csrf_token'] = random_string
    return session['_csrf_token']


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.pop('_csrf_token', None)
        if not token or token != request.form.get('_csrf_token'):
            logging.warning("Wrong CSRF token")
            return 'Sorry, wrong CSRF token', 400
    elif request.path == "/showEvents":
        session.pop('_csrf_token', None)


@app.template_filter('status')
def _jinja2_filter_retrieve_status(event):
    for attendee in event['attendees']:
        if 'self' in attendee:
            return attendee['responseStatus']
    return 'unknown'


@app.template_filter('format_dates')
def _jinja2_filter_format_dates(event):
    datetime_format = '%Y-%m-%d %H:%M'
    time_format = '%H:%M'
    if 'date' in event['start']:
        start = dateutil.parser.parse(event['start']['date']).replace(tzinfo=None)
        end = dateutil.parser.parse(event['end']['date']).replace(tzinfo=None)
        end += datetime.timedelta(days=-1)
        if start.date() == end.date():
            return event['start']['date']
        else:
            return event['start']['date'] + " - " + event['end']['date']

    start = dateutil.parser.parse(event['start']['dateTime']).replace(tzinfo=None)
    end = dateutil.parser.parse(event['end']['dateTime']).replace(tzinfo=None)
    if start.date() == end.date():
        return start.strftime(datetime_format) + "-" + end.strftime(time_format)
    else:
        return start.strftime(datetime_format) + " - " + end.strftime(datetime_format)


def get_user_info():
    if 'username' in session and 'email' in session:
        return session['username'], session['email']
    try:
        oauth2_service = build('oauth2', 'v2', http=oauth2.http())
        user_info = oauth2_service.userinfo().get().execute()
        session['username'] = user_info['name']
        session['email'] = user_info['email']
        return user_info['name'], user_info['email']
    except (ValueError, HttpAccessTokenRefreshError), e:
        logging.debug("Unable to oauth (%s), switching to users service" % str(e))

    user = users.get_current_user()
    return user.nickname(), user.email()


def render_events(context):
    public_events = [create_event(context, context.message, context.email, context.email)]
    public_events += create_additional_events(context)
    public_events_created = sum(not event.exists for event in public_events)

    events = get_events(context, context.email)
    rejected_events = get_rejected_events(context, events)
    cancelled_events = get_cancelled_events(context, events)

    received_error = execute_requests(context, rejected_events[1] + cancelled_events[1])

    logging.info("Created %d events, rejected %d events, cancelled %d events" % (len(public_events), len(rejected_events[0]), len(cancelled_events[0])))
    return render_template('events.html',
                           logout_url=users.create_logout_url('/'),
                           received_error=received_error,
                           holiday_context=context,
                           public_events=public_events,
                           public_events_created=public_events_created,
                           rejected_events=rejected_events[0],
                           cancelled_events=cancelled_events[0],
                           start_date=context.start_date.strftime('%Y-%m-%d'),
                           end_date=context.end_date.strftime('%Y-%m-%d'),
                           csrf_token=generate_csrf_token()).encode('utf-8')


def create_common_context(calendar_service=None):
    if request.method == 'POST':
        start = request.form.get('start', None)
        end = request.form.get('end', None)
        message = request.form.get('message', 'Holiday')
        search_calendars = request.form.get('searchCalendars', 'Holiday')
        dry_run = False
    else:
        start = request.args.get('start', None)
        end = request.args.get('end', None)
        message = request.args.get('message', 'Holiday')
        search_calendars = request.args.get('searchCalendars', 'Holiday')
        dry_run = True

    start_date = None
    try:
        start_date = dateutil.parser.parse(start)
    except (AttributeError, ValueError):
        pass

    end_date = None
    try:
        end_date = dateutil.parser.parse(end)
    except (AttributeError, ValueError):
        pass

    user_info = get_user_info()
    logging.debug("start: %s, end: %s, start_date: %s, end_date: %s, message: %s, dry_run: %s" % (start, end, start_date, end_date, message, dry_run))
    return HolidayContext(calendar_service, start_date, end_date, user_info[1], user_info[0], message, search_calendars, dry_run)


def validate_date(date):
    try:
        dateutil.parser.parse(date)
        return date
    except (AttributeError, ValueError):
        return ''


@app.route('/calendar-cleaner/clearEvents', methods=['GET', 'POST'])
@oauth2.required
def clear_events():
    if request.method == 'GET':
        return redirect("/calendar-cleaner")
    if oauth2.credentials.access_token_expired:
        logging.info("Refreshing token")
        session.clear()
        return oauth2.authorize_view()

    calendar_service = build('calendar', 'v3', http=oauth2.http(timeout=20))
    context = create_common_context(calendar_service)

    if context.start_date and context.end_date:
        return render_events(context)
    raise ValueError("Dates have incorrect format")


@app.route('/calendar-cleaner/showEvents', methods=['GET'])
@oauth2.required
def show_events():
    if oauth2.credentials.access_token_expired:
        logging.info("Refreshing token")
        session.clear()
        return redirect(oauth2.authorize_url(request.url))

    calendar_service = build('calendar', 'v3', http=oauth2.http(timeout=20))
    context = create_common_context(calendar_service)

    if context.start_date and context.end_date:
        try:
            return render_events(context)
        except HttpAccessTokenRefreshError, e:
            logging.exception("Received HttpAccessTokenRefreshError: {}. Redirecting to authorize_view".format(e))
            session.clear()
            return redirect(oauth2.authorize_url(request.url))

    else:
        start = validate_date(request.args.get('start', None))
        end = validate_date(request.args.get('end', None))

        return render_template('input_form.html',
                               logout_url=users.create_logout_url('/'),
                               holiday_context=context,
                               start_date=start,
                               end_date=end).encode('utf-8')


@app.route('/', methods=['GET'])
def root():
    return redirect("/calendar-cleaner")


@app.route('/calendar-cleaner', methods=['GET'])
def index():
    start = validate_date(request.args.get('start', None))
    end = validate_date(request.args.get('end', None))
    context = create_common_context()

    return render_template('input_form.html',
                           logout_url=users.create_logout_url('/'),
                           holiday_context=context,
                           start_date=start,
                           end_date=end).encode('utf-8')


@app.route('/calendar-cleaner/about', methods=['GET'])
def about():
    user_info = get_user_info()
    context = HolidayContext(email=user_info[1], name=user_info[0])

    return render_template('about.html',
                           version=modules.get_current_version_name(),
                           git_commit=settings.GIT_COMMIT,
                           build_date=settings.BUILD_DATE,
                           holiday_context=context).encode('utf-8')


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error404.html'), 404


@app.errorhandler(500)
def application_error(e):
    return render_template('error500.html',
                           error_message='{}'.format(e),
                           request_id=os.environ.get('REQUEST_LOG_ID')), 500


@app.after_request
def apply_common_headers(response):
    response.headers["Strict-Transport-Security"] = "max-age=10886400; includeSubDomains; preload"
    return response
