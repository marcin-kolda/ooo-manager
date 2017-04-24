import datetime
import logging
import re

from google.appengine.runtime.apiproxy_errors import DeadlineExceededError


class HolidayContext:
    def __init__(self, calendar_service=None, start_date=None, end_date=None, email=None, name=None, message=None, search_calendars=None, dry_run=None):
        self.calendar_service = calendar_service
        self.start_date = start_date
        self.end_date = end_date
        self.email = email
        self.name = name
        self.message = message
        self.search_calendars = search_calendars
        self.dry_run = dry_run


class Event:
    def __init__(self, summary, calendar_name, start, end, calendar_link, event_link='', exists=False):
        self.summary = summary
        self.calendar_name = calendar_name
        self.start = start
        self.end = end
        self.calendar_link = calendar_link
        self.event_link = event_link
        self.exists = exists


def handle_batch_request(request_id, response, exception):
    if exception is not None:
        logging.warn("Received exception %s" % str(exception))
    else:
        pass


def execute_requests(context, requests):
    received_error = False
    if not requests:
        return
    batch_count = 15
    logging.info("Executing %d requests batched by %d requests" % (len(requests), batch_count))
    count = 0
    batch = context.calendar_service.new_batch_http_request(callback=handle_batch_request)
    for rq in requests:
        batch.add(rq)
        count += 1
        if count >= batch_count:
            logging.debug("Executing %d requests in batch" % count)
            try:
                batch.execute()
            except DeadlineExceededError, e:
                logging.warn("Received '%s' exception" % str(e))
                received_error = True
            batch = context.calendar_service.new_batch_http_request(callback=handle_batch_request)
            count = 0
    if count > 0:
        logging.debug("Executing %d requests in batch" % count)
        try:
            batch.execute()
        except DeadlineExceededError, e:
            logging.warn("Received '%s' exception" % str(e))
            received_error = True
    return received_error


def should_cancel(event):
    if 'self' in event['organizer'] and 'attendees' in event:
        count = 0
        for attendee in event['attendees']:
            if 'self' in attendee or 'resource' in attendee:
                continue
            if re.match('[a-zA-Z0-9_-]+@', attendee['email']):
                logging.debug("Found group email: %s, skipping event" % attendee['email'])
                return False
            if attendee['responseStatus'] != 'declined':
                count += 1
        if count <= 1:
            return True

    return False


def get_cancelled_events(context, events):
    logging.info("Searching for unnecessary events which can be cancelled (e.g. One on one's)")
    cancelled_events = []
    requests = []
    for event in events:
        if should_cancel(event):
            event['updates'] = ['cancel', 'notification']
            cancelled_events.append(event)
            if not context.dry_run:
                requests.append(context.calendar_service.events().delete(calendarId=context.email, eventId=event['id'], sendNotifications=True))
    if not cancelled_events:
        logging.info('There is no any meeting which could be cancelled')
    return cancelled_events, requests


def should_reject(event):
    if 'attendees' in event:
        for attendee in event['attendees']:
            if 'self' in attendee and attendee['responseStatus'] == 'declined':
                return False
    else:
        return False
    return True


def should_grant_modify_rights_to_guests(event):
    if 'self' not in event['organizer']:
        return False
    if 'guestsCanModify' not in event:
        return True


def get_rejected_events(context, events):
    logging.info('Searching for events which can be rejected')
    rejected_events = []
    requests = []
    for event in events:
        if should_cancel(event):
            continue
        if should_reject(event):
            rejected_events.append(event)
            event['updates'] = ['reject']
            if 'self' not in event['organizer']:
                event['updates'].append('notification')
            if should_grant_modify_rights_to_guests(event):
                grant_modify_rights_to_guests = True
                event['updates'].append('edit')
            else:
                grant_modify_rights_to_guests = False
            if not context.dry_run:
                requests.append(reject_event(context, event['id'], event['attendees'], grant_modify_rights_to_guests))
    if not rejected_events:
        logging.info('There is no any meeting which needs to be rejected')
    return rejected_events, requests


def reject_event(context, event_id, attendees, grant_modify_rights_to_guests=False):
    for attendee in attendees:
        if 'self' in attendee:
            attendee['responseStatus'] = 'declined'
            attendee['comment'] = context.message
            break
    event = {
        'attendees': attendees,
    }
    if grant_modify_rights_to_guests:
        event['guestsCanModify'] = True
    return context.calendar_service.events().patch(calendarId=context.email, eventId=event_id, sendNotifications=True,
                                                   body=event)


def get_events(context, calendar_id, query=None):
    start = context.start_date.isoformat() + 'Z'
    end = (context.end_date + datetime.timedelta(days=1)).isoformat() + 'Z'
    fields = 'items(attendees,end,guestsCanInviteOthers,guestsCanModify,guestsCanSeeOtherGuests,recurringEventId,htmlLink,id,organizer,start,summary)'
    events_result = context.calendar_service.events().list(
            calendarId=calendar_id, timeMin=start, timeMax=end, fields=fields,
            maxResults=300, singleEvents=True,
            orderBy='startTime', q=query).execute()
    return events_result.get('items', [])


def create_event(context, summary, calendar_id, calendar_summary):
    start = context.start_date.strftime("%Y-%m-%d")
    end = context.end_date.strftime("%Y-%m-%d")
    if context.email == calendar_id:
        calendar_link = "https://calendar.google.com"
    else:
        calendar_link = "https://calendar.google.com/calendar/embed?src=" + calendar_id

    events = get_events(context, calendar_id, summary)
    for event in events:
        if summary == event['summary']:
            logging.debug("Event '%s' in calendar %s already exists" % (summary.encode('utf-8'), calendar_summary.encode('utf-8')))
            return Event(summary, calendar_summary, start, end, calendar_link, event['htmlLink'], exists=True)
    event = {
        'summary': summary,
        'start': {
            'date': context.start_date.strftime("%Y-%m-%d"),
        },
        'end': {
            'date': (context.end_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        },
        'visibility': 'public',
        'reminders': {
            'useDefault': False
        }
    }

    html_link = ''
    if not context.dry_run:
        event = context.calendar_service.events().insert(calendarId=calendar_id, body=event).execute()
        html_link = event['htmlLink']
    logging.debug("Event '%s' created in calendar '%s'" % (summary.encode('utf-8'), calendar_summary.encode('utf-8')))
    return Event(summary, calendar_summary, start, end, calendar_link, html_link)


def create_additional_events(context):
    events = []
    logging.debug("Searching subscribed calendars for '%s'" % context.search_calendars)
    calendars = context.calendar_service.calendarList().list(minAccessRole='writer').execute()
    for calendar in calendars['items']:
        if context.search_calendars.lower() in calendar['summary'].lower():
            summary = context.name + " - " + context.message
            events.append(create_event(context, summary, calendar['id'], calendar['summary']))
    return events
