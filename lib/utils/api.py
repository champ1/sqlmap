#!/usr/bin/env python

"""
Copyright (c) 2006-2012 sqlmap developers (http://sqlmap.org/)
See the file 'doc/COPYING' for copying permission
"""

import json
import logging
import os
import shutil
import sys
import StringIO
import tempfile
import threading
import types

_multiprocessing = None
try:
    import multiprocessing

    # problems on FreeBSD (Reference: http://www.eggheadcafe.com/microsoft/Python/35880259/multiprocessing-on-freebsd.aspx)
    _ = multiprocessing.Queue()
except (ImportError, OSError):
    pass
else:
    _multiprocessing = multiprocessing

from extra.bottle.bottle import abort
from extra.bottle.bottle import error
from extra.bottle.bottle import get
from extra.bottle.bottle import hook
from extra.bottle.bottle import post
from extra.bottle.bottle import request
from extra.bottle.bottle import response
from extra.bottle.bottle import run
from extra.bottle.bottle import static_file
from extra.bottle.bottle import template
from lib.controller.controller import start
from lib.core.common import unArrayizeValue
from lib.core.convert import hexencode
from lib.core.convert import stdoutencode
from lib.core.data import paths
from lib.core.datatype import AttribDict
from lib.core.data import kb
from lib.core.data import logger
from lib.core.defaults import _defaults
from lib.core.log import FORMATTER
from lib.core.log import LOGGER_HANDLER
from lib.core.log import LOGGER_OUTPUT
from lib.core.exception import SqlmapMissingDependence
from lib.core.optiondict import optDict
from lib.core.option import init
from lib.core.settings import UNICODE_ENCODING

RESTAPI_SERVER_HOST = "127.0.0.1"
RESTAPI_SERVER_PORT = 8775

# Local global variables
adminid = ""
tasks = AttribDict()

# Generic functions
def jsonize(data):
    return json.dumps(data, sort_keys=False, indent=4)

def is_admin(taskid):
    global adminid
    if adminid != taskid:
        return False
    else:
        return True

def init_options():
    dataype = {"boolean": False, "string": None, "integer": None, "float": None}
    options = AttribDict()

    for _ in optDict:
        for name, type_ in optDict[_].items():
            type_ = unArrayizeValue(type_)
            options[name] = _defaults.get(name, dataype[type_])

    # Enforce batch mode and disable coloring
    options.batch = True
    options.disableColoring = True

    return options

def start_scan():
    # Wrap logger stdout onto a custom file descriptor (LOGGER_OUTPUT)
    def emit(self, record):
        message = stdoutencode(FORMATTER.format(record))
        print >>LOGGER_OUTPUT, message.strip('\r')

    LOGGER_HANDLER.emit = types.MethodType(emit, LOGGER_HANDLER, type(LOGGER_HANDLER))

    # Wrap standard output onto a custom file descriptor
    sys.stdout = open(str(os.getpid()) + ".out", "wb")
    #sys.stderr = StringIO.StringIO()

    taskid = multiprocessing.current_process().name
    init(tasks[taskid], True)
    start()

@hook("after_request")
def security_headers():
    """
    Set some headers across all HTTP responses
    """
    response.headers["Server"] = "Server"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Pragma"] = "no-cache"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Expires"] = "0"
    response.content_type = "application/json; charset=UTF-8"

##############################
# HTTP Status Code functions #
##############################

@error(401) # Access Denied
def error401(error=None):
    return "Access denied"

@error(404) # Not Found
def error404(error=None):
    return "Nothing here"

@error(405) # Method Not Allowed (e.g. when requesting a POST method via GET)
def error405(error=None):
    return "Method not allowed"

@error(500) # Internal Server Error
def error500(error=None):
    return "Internal server error"

#############################
# Task management functions #
#############################

# Users' methods
@get("/task/new")
def task_new():
    """
    Create new task ID
    """
    global tasks

    taskid = hexencode(os.urandom(16))
    tasks[taskid] = init_options()

    return jsonize({"taskid": taskid})

@get("/task/<taskid>/destroy")
def task_destroy(taskid):
    """
    Destroy own task ID
    """
    if taskid in tasks and not is_admin(taskid):
        tasks.pop(taskid)
        return jsonize({"success": True})
    else:
        abort(500, "Invalid task ID")

# Admin's methods
@get("/task/<taskid>/list")
def task_list(taskid):
    """
    List all active tasks
    """
    if is_admin(taskid):
        return jsonize({"tasks": tasks})
    else:
        abort(401)

@get("/task/<taskid>/flush")
def task_flush(taskid):
    """
    Flush task spool (destroy all tasks except admin)
    """
    global adminid
    global tasks

    if is_admin(taskid):
        admin_task = tasks[adminid]
        tasks = AttribDict()
        tasks[adminid] = admin_task

        return jsonize({"success": True})
    else:
        abort(401)

##################################
# sqlmap core interact functions #
##################################

# Admin's methods
@get("/status/<taskid>")
def status(taskid):
    """
    Verify the status of the API as well as the core
    """

    if is_admin(taskid):
        busy = kb.get("busyFlag")
        tasks_num = len(tasks)
        return jsonize({"busy": busy, "tasks": tasks_num})
    else:
        abort(401)

@get("/cleanup/<taskid>")
def cleanup(taskid):
    """
    Destroy all sessions except admin ID and all output directories
    """
    global tasks

    if is_admin(taskid):
        for task, options in tasks.items():
            if "oDir" in options and options.oDir is not None:
                shutil.rmtree(options.oDir)

        admin_task = tasks[adminid]
        tasks = AttribDict()
        tasks[adminid] = admin_task

        return jsonize({"success": True})
    else:
        abort(401)

# Functions to handle options
@get("/option/<taskid>/list")
def option_list(taskid):
    """
    List options for a certain task ID
    """
    if taskid not in tasks:
        abort(500, "Invalid task ID")

    return jsonize(tasks[taskid])

@post("/option/<taskid>/get")
def option_get(taskid):
    """
    Get the value of an option (command line switch) for a certain task ID
    """
    if taskid not in tasks:
        abort(500, "Invalid task ID")

    option = request.json.get("option", "")

    if option in tasks[taskid]:
        return jsonize({option: tasks[taskid][option]})
    else:
        return jsonize({option: None})

@post("/option/<taskid>/set")
def option_set(taskid):
    """
    Set an option (command line switch) for a certain task ID
    """
    global tasks

    if taskid not in tasks:
        abort(500, "Invalid task ID")

    for key, value in request.json.items():
        tasks[taskid][key] = value

    return jsonize({"success": True})

# Function to handle scans
@post("/scan/<taskid>/start")
def scan_start(taskid):
    """
    Launch a scan
    """
    global tasks

    if taskid not in tasks:
        abort(500, "Invalid task ID")

    # Initialize sqlmap engine's options with user's provided options
    # within the JSON request
    for key, value in request.json.items():
        tasks[taskid][key] = value

    # Overwrite output directory (oDir) value to a temporary directory
    tasks[taskid].oDir = tempfile.mkdtemp(prefix="sqlmap-")

    # Launch sqlmap engine in a separate thread
    logger.debug("starting a scan for task ID %s" % taskid)

    if _multiprocessing:
        #_multiprocessing.log_to_stderr(logging.DEBUG)
        p = _multiprocessing.Process(name=taskid, target=start_scan)
        p.daemon = True
        p.start()
        p.join()

    return jsonize({"success": True})

@get("/scan/<taskid>/output")
def scan_output(taskid):
    """
    Read the standard output of sqlmap core execution
    """
    global tasks

    if taskid not in tasks:
        abort(500, "Invalid task ID")

    sys.stdout.seek(0)
    output = sys.stdout.read()
    sys.stdout.flush()
    sys.stdout.truncate(0)

    return jsonize({"output": output})

@get("/scan/<taskid>/delete")
def scan_delete(taskid):
    """
    Delete a scan and corresponding temporary output directory
    """
    global tasks

    if taskid not in tasks:
        abort(500, "Invalid task ID")

    if "oDir" in tasks[taskid] and tasks[taskid].oDir is not None:
        shutil.rmtree(tasks[taskid].oDir)

    return jsonize({"success": True})

# Function to handle scans' logs
@get("/scan/<taskid>/log")
def scan_log(taskid):
    """
    Retrieve the log messages
    """
    if taskid not in tasks:
        abort(500, "Invalid task ID")

    LOGGER_OUTPUT.seek(0)
    output = LOGGER_OUTPUT.read()
    LOGGER_OUTPUT.flush()
    LOGGER_OUTPUT.truncate(0)

    return jsonize({"log": output})

# Function to handle files inside the output directory
@get("/download/<taskid>/<target>/<filename:path>")
def download(taskid, target, filename):
    """
    Download a certain file from the file system
    """
    if taskid not in tasks:
        abort(500, "Invalid task ID")

    # Prevent file path traversal - the lame way
    if target.startswith("."):
        abort(500)

    path = os.path.join(paths.SQLMAP_OUTPUT_PATH, target)
    if os.path.exists(path):
        return static_file(filename, root=path)
    else:
        abort(500)

def server(host="0.0.0.0", port=RESTAPI_SERVER_PORT):
    """
    REST-JSON API server
    """
    global adminid
    global tasks

    adminid = hexencode(os.urandom(16))
    tasks[adminid] = init_options()

    logger.info("running REST-JSON API server at '%s:%d'.." % (host, port))
    logger.info("the admin task ID is: %s" % adminid)

    # Run RESTful API
    run(host=host, port=port, quiet=False, debug=False)

def client(host=RESTAPI_SERVER_HOST, port=RESTAPI_SERVER_PORT):
    """
    REST-JSON API client
    """
    addr = "http://%s:%d" % (host, port)
    logger.info("starting debug REST-JSON client to '%s'..." % addr)

    # TODO: write a simple client with requests, for now use curl from command line
    logger.error("not yet implemented, use curl from command line instead for now, for example:")
    print "\n\t$ curl http://%s:%d/task/new" % (host, port)
    print "\t$ curl -H \"Content-Type: application/json\" -X POST -d '{\"url\": \"http://testphp.vulnweb.com/artists.php?artist=1\"}' http://%s:%d/scan/:taskid/start" % (host, port)
    print "\t$ curl http://%s:%d/scan/:taskid/output" % (host, port)
    print "\t$ curl http://%s:%d/scan/:taskid/log\n" % (host, port)
