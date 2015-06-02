#! /usr/bin/python

#
# This file is part of NoTS.
#
# NoTS is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# NoTS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with NoTS.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import json
import bson
import uuid
import re
import datetime
import time
from collections import OrderedDict
from optparse import OptionParser
from flask import Flask, Response
from flask import session, request, redirect, url_for, current_app, make_response, abort
import pymongo

# Pseudo-JSON compression data
VALUE_TABLE = {
    '@i': '@id',
    '@t': '@type',
    '@b': 'begin',
    # Note: in compact mode, @d is in fact the duration (end - begin)
    '@d': 'end',
    '@s': 'subject',
}

# Server configuration
CONFIG = {
    'database': 'ktbs',
    # Enable debug. This implicitly disallows external access
    'enable_debug': False,
    # Run the server in external access mode (i.e. not only localhost)
    'allow_external_access': True,
    # Trace access control (for reading) is either:
    # 'none' -> no access
    # 'localhost' -> localhost only
    # 'any' -> any host
    'trace_access_control': 'none',
    'port': 5001,
}

MAX_DEFAULT_OBSEL_COUNT = 1000

connection = pymongo.MongoClient("localhost", 27017)
db = None

app = Flask(__name__)

class MongoEncoder(json.JSONEncoder):
    def default(self, obj, **kwargs):
        if isinstance(obj, bson.ObjectId):
            return str(obj)
        else:
            return json.JSONEncoder.default(obj, **kwargs)

@app.errorhandler(401)
def custom_401(error):
    return Response('Unauthorized access', 401, {'WWWAuthenticate':'Basic realm="Login Required"'})

@app.route("/", methods= [ 'GET', 'HEAD', 'OPTIONS' ])
def index():
    if request.method == 'HEAD' or request.method == 'OPTIONS':
        return Response('', 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
        })
    if 'userinfo' in session:
        #return 'Logged in as : %s' % escape(session['navigator'])
        #session['navigator']['id']="test";
        return Response("Logged in as " + session['userinfo']['id'], 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
        })
    return 'You are not logged in'

@app.route('/login', methods = ['GET', 'POST'])
def login():
    # 'userinfo' is either a (GET) named param, or a (POST) form
    # field, whose value contains JSON data with information about
    # the user
    params = request.values.get('userinfo', '{"default_subject":"anonymous"}')

    if 'userinfo' in session:
        # session was already initialized. Update its information.
        d = json.loads(params)
        d['id'] = session['userinfo']['id']
        db['userinfo'].update( {"id": session['userinfo']['id']}, d)
        session['userinfo'].update(d)
        session.modified = True
    else:
        session['userinfo'] = json.loads(params)
        session['userinfo'].setdefault('id', str(uuid.uuid1()))

        db['userinfo'].save(dict(session['userinfo']))
        session.modified = True

    # Current time in ms. It may be different from times sent by
    # client, because of different timezones or even clock skew. It is
    # indicative.
    t = long(time.time() * 1000)
    db['trace'].save({ '_serverid': session['userinfo'].get('id', ""),
                       '@type': 'Login',
                       'begin': t,
                       'end': t,
                       'subject': session['userinfo'].get('default_subject', "anonymous")
                       })
    app.logger.debug("Logged in as " + session['userinfo']['id'])
    return redirect(url_for('index'))

def iter_obsels(cursor):
    for o in cursor:
        o['@id'] = o['_id']
        del o['_id']
        o['session'] = o['_serverid']
        del o['_serverid']
        yield o

@app.route('/trace/', methods= [ 'POST', 'GET', 'HEAD', 'OPTIONS' ])
def trace():
    if request.method == 'OPTIONS':
        return Response('', 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
        })
    if (request.method == 'POST' or
        (request.method == 'GET' and 'data' in request.values)):
        # Handle posting obsels to the trace
        # FIXME: security issue -must check request.content_length
        if not 'userinfo' in session:
            # No explicit login. Generate a session id
            session['userinfo'] = {'id': str(uuid.uuid1())}
            db['userinfo'].save(dict(session['userinfo']))
        if request.method == 'POST':
            obsels = request.json or []
        else:
            data = request.values.get('post') or request.values.get('data', "")
            if data.startswith('c['):
                # Data mangling here. Pseudo compression is involved.
                # Swap " and ;. Note that we use unicode.translate, so we pass a dict mapping.
                data = data[1:].translate({ord(u'"'): u';', ord(u';'):u'"'}).replace('%23', '#')
                # Replace keys with matching values
                obsels = [ dict((VALUE_TABLE.get(k, k), v) for k, v in o.iteritems() )
                           for o in json.loads(data) ]
                # Decode optional relative ends: if end is not
                # present, then it is the same as begin. If present,
                # it is encoded as duration.
                for o in obsels:
                    o['end'] = o.get('end', 0) + o['begin']
                    if not 'id' in o:
                        o['id'] = ""
                    if not 'subject' in o:
                        o['subject'] = session['userinfo'].get('default_subject', "anonymous")
            elif data:
                obsels = json.loads(data)
            else:
                obsels = []
        for obsel in obsels:
            obsel['_serverid'] = session['userinfo'].get('id', "");
            db['trace'].save(obsel)
        response = make_response()
        response.headers['X-Obsel-Count'] = str(len(obsels))
        response.headers['Access-Control-Allow-Origin'] = '*'
        if request.method == 'GET':
            # GET methods are usually used to make cross-site
            # requests, and invoked through a <img> src
            # attribute. Let's return a pseudo-image.
            response.mimetype = 'image/png'
            # Return the smalled valid PNG file, see http://garethrees.org/2007/11/14/pngcrush/
            response.data = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        else:
            response.data = "%d" % len(obsels)
        return response
    elif request.method == 'GET':
        if (CONFIG['trace_access_control'] == 'any'
            or (CONFIG['trace_access_control'] == 'localhost' and request.remote_addr == '127.0.0.1')):
            detail = request.values.get('detail', False)
            return "\n".join( generate_trace_index_document(detail=detail) )
        else:
            abort(401)
    elif request.method == 'HEAD':
        if (CONFIG['trace_access_control'] == 'any'
            or (CONFIG['trace_access_control'] == 'localhost' and request.remote_addr == '127.0.0.1')):
            response = make_response()
            count = db['trace'].count()
            response.headers['Content-Range'] = "items 0-%d/%d" % (max(count - 1, 0), count)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        else:
            abort(401)

def generate_trace_index_document(detail=False):
    yield """<b>Available subjects:</b>\n<ul>"""
    stat = get_stats()
    for s in stat['subjects']:
        yield """<li><a href="%s">%s</a> (%d obsels between %s and %s)</li>""" % (s['id'],
                                                                                  s['id'],
                                                                                  s['obselCount'],
                                                                                  format_time(s['minTimestamp']),
                                                                                  format_time(s['maxTimestamp']))
    yield """</ul>"""

@app.route('/stat/user/', methods= [ 'GET' ])
def users_stats(user=None):
    """Return user stats
    """
    return current_app.response_class(json.dumps(get_stats()),
                                      mimetype='application/json')

@app.route('/stat/user/<path:user>', methods= [ 'GET' ])
def user_stats(user):
    """Return detailed stats (by day) for the given user.
    """
    aggr = db['trace'].aggregate( [
            { '$match': { 'begin': { '$ne': 0 },
                          'subject': user } },
            { '$group':
              { '_id': '$subject',
                'min': { '$min': '$begin' },
                'max': { '$max': '$end' },
                'obselCount': { '$sum': 1 }
                }
              }
            ] )
    ranges = []

    t = time.localtime(aggr['result'][0]['max'] / 1000)
    tmax = datetime.datetime(*t[:7])
    t = time.localtime(aggr['result'][0]['min'] / 1000)
    dt = datetime.datetime(*t[:7])

    while dt < tmax:
        begin = long(1000 * time.mktime(dt.timetuple()))
        dt = dt + datetime.timedelta(1)
        end = long(1000 * time.mktime(dt.timetuple()))
        count = db['trace'].find({ 'subject': user,
                                   'begin': { '$gt': begin },
                                   'end': { '$lt': end } }).count()
        if count > 0:
            ranges.append({ 'date': str(dt.date()),
                            'obselCount': count })

    return current_app.response_class(json.dumps({ 'subject': user,
                                                   'ranges': ranges }),
                                      mimetype='application/json')

def format_time(ts):
    """Format a timestamp in ms to a string.
    """
    t = time.localtime(long(ts) / 1000)
    dt = datetime.datetime(*t[:7])
    return str(dt.isoformat())

def ts_to_ms(ts, is_ending_timestamp=False):
    """Convert a timestamp to ms.

    This function supports a number of formats:
    * plain numbers (considered as ms)
    * YYYY/MM/DD

    Its behaviour may differ when considering start or end
    timestamps. is_ending_timestamp indicates when we are in the
    latter case.
    """
    if ts is None:
        return None

    try:
        ms = long(ts)
    except ValueError:
        m = re.match('(\d\d\d\d)/(\d\d?)/(\d\d?)', ts)
        if m is not None:
            l = [ int(n) for n in m.groups() ]
            d = datetime.datetime(*l)
            if is_ending_timestamp:
                # Ending timestamp: consider begin of following day
                # instead
                d = d + datetime.timedelta(1)
            ms = long(1000 * time.mktime(d.timetuple()))
        else:
            ms = None
    return ms

@app.route('/trace/<path:info>', methods= [ 'GET', 'HEAD' ])
def trace_get(info):
    if CONFIG['trace_access_control'] == 'none':
        abort(401)
    if (CONFIG['trace_access_control'] == 'localhost' and request.remote_addr != '127.0.0.1'):
        abort(401)

    # For paging: http://stackoverflow.com/questions/5049992/mongodb-paging
    # Parameters: page / pageSize or from=timestamp / to=timestamp
    # In the first case (page), the returned Content-Range will indicate
    #  items start-end/total
    # In the second case (from/to), the returned Content-Range will indicate
    #  items 0-(count-1)/total
    # where total is the total number of obsels in the given subject's trace
    # and count is the number of items matching the request

    # TODO: Find a way to return a summarized representation if interval is too large.
    from_ts = ts_to_ms(request.values.get('from', None))
    to_ts = ts_to_ms(request.values.get('to', None), True)
    page_size = request.values.get('pageSize', 100)
    if page_size is not None:
        page_size = int(page_size)
    page_number = request.values.get('page', None)
    if page_number is not None:
        page_number = int(page_number)
    info = info.split('/')
    query = {}
    if from_ts is not None:
        query['begin'] =  { '$gt': from_ts }
    if to_ts is not None:
        query['end'] =  { '$lt': to_ts }

    if len(info) == 1 or (len(info) == 2 and info[1] == ''):
        if info[0] and info[0] != '@obsels':
            query = { 'subject': info[0] }
        
        obsels = db['trace'].find(query)
        total = obsels.count()

        if page_number is not None:
            # User requested a specific page number.
            if page_number > 0:
                i = page_number * page_size
            else:
                i = total + page_number * page_size
            if i > total or i < 0:
                # Requested Range Not Satisfiable
                abort(416)
            else:
                if request.method == 'HEAD':
                    response = make_response()
                    end = min(i + page_size, total)
                    response.headers['Content-Range'] = "items %d-%d/%d" % (i, max(end - 1, 0), total)
                    response.headers['Content-Type'] = 'application/json'
                    response.headers['Access-Control-Allow-Origin'] = '*'
                    return response
                else:
                    # Note: if we use the common codepath (just
                    # setting cursor), then the Content-Range will
                    # start at 0 -> wrong info. So we have to generate
                    # the response here
                    cursor = obsels.skip(i).limit(page_size)
                    count = cursor.count()
                    response = current_app.response_class( json.dumps({
                                "@context": [
                                    "http://liris.cnrs.fr/silex/2011/ktbs-jsonld-context",
                                    #{ "m": "http://localhost:8001/base1/model1#" }
                                    ],
                                "@id": ".",
                                "hasObselList": "",
                                'obsels': list(iter_enriched_obsels(cursor)) },
                                                                  indent=None if request.is_xhr else 2,
                                                                  cls=MongoEncoder),
                                                           mimetype='application/json')
                    response.headers['Content-Range'] = "items %d-%d/%d" % (i, i + count, total)
                    response.headers['Content-Type'] = 'application/json'
                    return response

        obsels = db['trace'].find(query)
        count = obsels.count()
        if request.method == 'HEAD':
            response = make_response()
            response.headers['Content-Range'] = "items 0-%d/%d" % (max(count - 1, 0), total)
            response.headers['Content-Type'] = 'application/json'
            return response
        else:
            if count > MAX_DEFAULT_OBSEL_COUNT and from_ts is None and to_ts is None and page_number is None:
                # No parameters were specified and the result is too large. Return a
                # 413 Request Entity Too Large
                abort(413)
            response = current_app.response_class( json.dumps({
                        "@context": [
                            "http://liris.cnrs.fr/silex/2011/ktbs-jsonld-context",
                            #{ "m": "http://localhost:8001/base1/model1#" }
                            ],
                        "@id": ".",
                        "hasObselList": "",
                        'obsels': list(iter_enriched_obsels(obsels)) },
                                                          indent=None if request.is_xhr else 2,
                                                          cls=MongoEncoder),
                                                   mimetype='application/json')
            response.headers['Content-Range'] = "items 0-%d/%d" % (max(count - 1, 0), total)
            return response
    elif len(info) == 2:
        # subject, id: let's ignore from/to parameters
        return current_app.response_class( json.dumps({
                    "@context": [
                        "http://liris.cnrs.fr/silex/2011/ktbs-jsonld-context",
                        #{ "m": "http://localhost:8001/base1/model1#" }
                    ],
                    "@id": ".",
                    "hasObselList": "",
                    "obsels": list(iter_enriched_obsels(db['trace'].find( { '_id': bson.ObjectId(info[1]) }))) },
                                   indent=None if request.is_xhr else 2,
                                   cls=MongoEncoder),
                                           mimetype='application/json')
    else:
        return "Got info: " + ",".join(info)

@app.route('/logout')
def logout():
    session.pop('userinfo', None)
    return redirect(url_for('index'))

def get_stats(args=None):
    aggr = db['trace'].aggregate( [
            { '$match': { 'begin': { '$ne': 0 } } },
            { '$group':
              { '_id': '$subject',
                'min': { '$min': '$begin' },
                'max': { '$max': '$end' },
                'obselCount': { '$sum': 1 }
                }
              }
            ] )
    return OrderedDict( [
            ('obselCount', db['trace'].find().count()),
            ('subjectCount', len(aggr['result'])),
            ('minTimestamp', min(r['min'] for r in aggr['result'])),
            ('maxTimestamp', min(r['max'] for r in aggr['result'])),
            ('subjects', [ { 'id': s['_id'],
                             'obselCount': s['obselCount'],
                             'minTimestamp': s['min'],
                             'maxTimestamp': s['max'] }
                           for s in aggr['result'] ])
            ])

def dump_stats(args):
    print json.dumps(get_stats(args), indent=2).encode('utf-8')

def enriched_obsels(args):
    """Enriched obsel iterator.

    It takes arguments as parameters, and returns as an iterator obsels, which are decorated with additional information such as media-id
    It returns a tuple (count, iterator)
    """
    opts = {}
    args = dict( a.split('=') for a in args )
    if args.get('subject'):
        opts['subject'] = args.get('subject')
    if args.get('from'):
        opts['begin'] = { '$gt': ts_to_ms(args.get('from')) }
    if args.get('to'):
        opts['end'] = { '$lt': ts_to_ms(args.get('to'), True) }

    cursor = db['trace'].find(opts)
    count = cursor.count()
    return (count, iter_enriched_obsels(cursor))

def iter_enriched_obsels(cursor):
    # Redecorate all values with media id or url info Mediaid is
    # indexed by session key. We try to update it for every obsel
    # where the info is present, or reconstruct it 
    mediaid = {}
    obsels = iter_obsels(cursor)
    for i, o in enumerate(obsels):
        o['date'] = format_time(o['begin'])
        if 'traceInfo' in o:
            for ex in re.split("\s*,\s*", o.get('traceInfo', "")):
                if ex:
                    l = ex.split(':')
                    if len(l) == 2:
                        o[l[0].strip()] = str(l[1].strip())
            del o['traceInfo']
        if 'url' in o:
            m = re.search('/contents/\w+/(\w+)', o['url'])
            if m:
                mid = m.group(1)
                if re.search('^\d', mid):
                    mid = "m" + mid
                mediaid[o['session']] = mid
        if 'media-id' in o and o['media-id'] != 'm1':
            mediaid[o['session']] = o['media-id'] = re.sub('^v_', '', o['media-id'])
        else:
            o['media-id'] = mediaid.get(o['session'], "unknown")
        yield o
    
def dump_turtle(args):
    (count, obsels) = enriched_obsels(args)
    for o in obsels:
        out = u"""@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ktbs: <http://liris.cnrs.fr/silex/2009/ktbs#> .
@prefix : <../model/> .

<%(id)s> a :%(name)s;
  ktbs:hasTrace <> ;
  ktbs:hasBegin %(begin)s;
  ktbs:hasEnd %(end)s;
  ktbs:hasSubject "%(subject)s";
  %(data)s
.
""" % {
            'id': o['@id'],
            'name': o['@type'],
            'begin': o['begin'],
            'end': o['end'],
            'subject': o['subject'],
            'data': u"\n  ".join( u':has%s %s;' % (name.capitalize(),
                                                   json.dumps(value))
                                  for (name, value) in o.iteritems()
                                  if not name in ('begin', 'end', '@type', '@id', 'id', 'subject'))
            }
        print out.encode('utf-8')

def dump_elasticsearch(args):
    (count, obsels) = enriched_obsels(args)
    for i, o in enumerate(obsels):
        o['@timestamp'] = o['begin'] = o['date']
        o['end'] = format_time(o['end'])
        o['@id'] = unicode(o['@id'])
        out = u"""{"index":{"_index":"%(base)s","_type":"%(type)s","_id":"%(index)d"}, "_timestamp": "%(timestamp)s"}
{ %(data)s }""" % {
    'base': CONFIG['database'],
    'type': o['@type'],
    'index': i + 1,
    'timestamp': o['@timestamp'],
    'data': u", ".join( u'"%s": %s' % (name,
                                     json.dumps(value))
                        for (name, value) in o.iteritems())
}
        print out.encode('utf-8')

def dump_db(args):
    """Dump all obsels from the database.
    """
    (count, obsels) = enriched_obsels(args)
    print """{
  "@context": [
     "http://liris.cnrs.fr/silex/2011/ktbs-jsonld-context"
  ],
  "@id": ".",
  "count": %d,
  "hasObselList": "",
  "obsels": [""" % count

    prefix = "    "

    # Emulate join behaviour but in a streaming mode
    try:
        current = obsels.next()
    except StopIteration:
        current = None
    try:
        nxt = obsels.next()
    except StopIteration:
        nxt = None

    while current is not None:
        print prefix + (json.dumps(current,
                                   indent=2,
                                   cls=MongoEncoder)
                        + ("," if nxt is not None else "")).replace("\n", "\n" + prefix)
        current = nxt
        try:
            nxt = obsels.next()
        except StopIteration:
            nxt = None

    print """  ]
}
"""

# set the secret key.  keep this really secret:
app.secret_key = os.urandom(24)

if __name__ == "__main__":
    parser=OptionParser(usage="""Trace server.\n%prog [options]\n\nThe from/to filters accept either plain integer timestamps (considered as ms) or YYYY/MM/DD syntax.""")

    parser.add_option("-b", "--base", dest="database", action="store",
                      help="Mongo database name.",
                      default="ktbs")

    parser.add_option("-p", "--port", dest="port", type="int", action="store",
                      help="Port number", default=5001)

    parser.add_option("-d", "--debug", dest="enable_debug", action="store_true",
                      help="Enable debug. This implicitly disallows external access.",
                      default=False)

    parser.add_option("-D", "--dump", dest="dump_db", action="store_true",
                      help="Dump database to stdout in JSON format. You can additionnaly specify one or many filters:\n  subject=foo: filter on subject\n  from=NNN: filter from the given timecode\n  to=NNN: filter to the given timecode",
                      default=False)

    parser.add_option("-E", "--elasticsearch", dest="dump_elasticsearch", action="store_true",
                      help="Dump database into ElasticSearch bulk import format (subject/from/to filters possible)",
                      default=False)

    parser.add_option("-T", "--dump-as-turtle", dest="dump_turtle", action="store_true",
                      help="Dump database to stdout in TTL format. You can additionnaly specify one or many filters:\n  subject=foo: filter on subject\n  from=NNN: filter from the given timecode\n  to=NNN: filter to the given timecode",
                      default=False)

    parser.add_option("-S", "--statistics", dest="dump_stats", action="store_true",
                      help="Display database statistics to stdout in JSON format.",
                      default=False)

    parser.add_option("-e", "--external", dest="allow_external_access", action="store_true",
                      help="Allow external access (from any host)", default=False)

    parser.add_option("-g", "--get-access-control",
                      action="store", type="choice", dest="trace_access_control",
                      choices=("none", "localhost", "any"), default='none',
                      help="""Control trace GET access. Values: none: no trace access; localhost: localhost only; any: any host can access""")

    (options, args) = parser.parse_args()
    if options.enable_debug:
        options.allow_external_access = False
    CONFIG.update(vars(options))

    db = connection[CONFIG['database']]

    if args and args[0] == 'shell':
        import pdb; pdb.set_trace()
        import sys; sys.exit(0)

    if options.dump_stats:
        dump_stats(args)
    elif options.dump_turtle:
        dump_turtle(args)
    elif options.dump_db:
        dump_db(args)
    elif options.dump_elasticsearch:
        dump_elasticsearch(args)
    else:
        print "Options:"
        for k, v in CONFIG.iteritems():
            print " %s: %s" % (k, str(v))

        if CONFIG['enable_debug']:
            app.run(debug=True)
        elif CONFIG['allow_external_access']:
            app.run(debug=False, host='0.0.0.0', port=CONFIG['port'])
        else:
            app.run(debug=False, port=CONFIG['port'])
