import json
import sys
import os
import time
import signal
import gevent
import urllib2
from gevent.queue import Queue
from importlib import import_module
import logging
import threading
from threading import Thread
from flask import Flask, Response, request, abort, copy_current_request_context
from flask_cors import *
from flask_socketio import SocketIO, emit, send, disconnect
from pkg_resources import Requirement, resource_filename
rootPath = resource_filename(Requirement.parse("omniui"),"")

app = Flask(__name__)
app.config['CORS_ORIGINS'] = ['*']
app.config['SECRET_KEY'] = 'omniui'
socketio = SocketIO(app)
subscriptions = []
client_alive = {}
logger = logging.getLogger(__name__)

# define module state enum
def enum(**enums):
	return type('Enum', (), enums)
Status = enum(PENDING=0,ACTIVE=1)

class Module:
	def __init__(self, name, status, dependencies):
		self.name = name
		self.status = status
		self.dependencies = dependencies

class ServerSentEvent(object):

	def __init__(self, data):
		self.id = None
		self.event = data['event']
		self.data = data['data']
		self.desc_map = {
			self.id: 'id',
			self.event: 'event',
			self.data: 'data'
		}

class EventHandler:
	def __init__(self,eventName,handler):
		self.eventName = eventName
		self.handler = handler

class Core:
	def __init__(self):
		#Local members
		self.eventHandlers = []
		self.threads  = []
		self.events   = []
		self.ipcHandlers = {}
		global urlHandlers # Necessary for bottle to access urlHandlers
		urlHandlers = {}
		global sseHandlers
		sseHandlers = {}

	def start(self):
		#Load config file
		with open(os.path.join(rootPath, 'etc/config.json'),'r') as input:
			data = input.read()
		config = json.loads(data)
		#Default values
		logFile = '/tmp/omniui.log'
		logLevel = logging.ERROR
		handleIP = 'localhost'
		handlePort = 5567
		ControllerType = ''
		#Set Logger
		if config.has_key("LogFile"):
			logFile = config['LogFile']
		logging.basicConfig(filename = logFile, level = logLevel, format = '%(asctime)s - %(levelname)s: %(message)s')
		console = logging.StreamHandler()
		console.setLevel(logging.DEBUG)
		#Modulize the controller adapter
		if config.has_key("ControllerType"):
			ControllerType = str(config['ControllerType']) + "_modules"

		"""
		ControllerType must be named like this:
		nox , pox , floodlight , opendaylight , ryu , trema ...etc, and core.py will append "_modules"

		i.e. pox_modules , floodlight_modules ...etc
		"""

		#Loading module
		sys.modules['plugins'] = plugins = type(sys)('plugins')
		plugins.__path__ = []
		plugins.__path__.append (os.path.join(rootPath, 'src', ControllerType))

		loadedModules = {}
		def loadModule(moduleName):
			if config[moduleName].has_key('depend'):
				module = Module(name=moduleName,status=Status.PENDING,dependencies=config[moduleName]['depend'])
			else:
				module = Module(name=moduleName,status=Status.PENDING,dependencies=[])
			if module.name in loadedModules.keys():
				return
			loadedModules[module.name]=module
			for dependency in module.dependencies:
				print("{} PENDING!!!".format(module.name))
				if dependency in loadedModules.keys() and loadedModules[dependency].status == Status.PENDING:
					print(module.name, dependency)
					print("dependencies loops, exit")
					os.kill(os.getpid(),1)
				loadModule(dependency)
			instance = import_module('plugins.' + module.name.lower())
			if(config.has_key(module.name)):
				getattr(instance,module.name)(self,config[module.name])
			else:
				print("module not found: {}".format(module.name))
				os.kill(os.getpid(),1)
			print("loading {}......".format(module.name))
			module.status = Status.ACTIVE
		# get module list
		for module in config:
			if module != "LogFile" and module != "WEBSOCKET" and module != "ControllerType" : # get modules other than LogFile and WEBSOCKET
				loadModule(module)
		# Start WEBSOCKET service
		if config.has_key("WEBSOCKET"):
			handleIP = config['WEBSOCKET']['ip']
			handlePort = config['WEBSOCKET']['port']

			def notify(e, d, q=None):
				msg = {
					'event': e,
					'data': d
				}
				if q is not None:
					q.put(msg)
				else:
					for sub in subscriptions[:]:
						sub.put(msg)
	
			@socketio.on('connect', namespace='/websocket')
			def do_connect():
				def gen():
					q = Queue()
					subscriptions.append(q)
					for e in sseHandlers.keys():
						rs = sseHandlers[e]('debut')
						if rs is not None:
							gevent.spawn(notify, e, rs, q)
					try:
						while True:
							result = q.get()
							ev = ServerSentEvent(result)
							yield ev
					except GeneratorExit:
						subscriptions.remove(q)
					except StopIteration:
						print('get subscription error.')
						subscriptions.remove(q)

				@copy_current_request_context
				def response(sid):
					responses = gen()
					client_alive[sid] = True
					responses = gen()
					while client_alive[sid]:
						r = responses.next()
						emit(r.event, {'data': r.data })
					responses.close()

				sid = request.sid
				print('Client ' + request.remote_addr + '(sid:' + str(sid) + ') connected')
				gevent.spawn(response, sid)

			@socketio.on('disconnect', namespace='/websocket')
			def do_disconnect():
				sid = request.sid
				if client_alive.get(sid, False):
				   client_alive[sid] = False
				print('Client ' + request.remote_addr + '(sid:' + str(sid) + ') disconnected')

			@socketio.on('debug', namespace='/websocket')
			def debug():
				emit('debug', {'data' : 'Currently %d subscriptions' % len(subscriptions) })

			# Receive data from SB
			@app.route('/publish/<event>', methods=['POST'])
			@cross_origin()
			def publish(event):
				if event in sseHandlers:

					#
					# Process data, push event
					#

					rs = sseHandlers[event](request.json)
					if rs is None:
						logger.warn('\'%s\' event has been ignored' % event)
						return 'OK'
					gevent.spawn(notify, event, rs)
				else:
					abort(404, 'No such event: \'/publish/%s\'' % event)

				return 'OK'

			# handler for feature request
			@socketio.on('feature', namespace='/websocket')
			def featureRequest():
				feature = {'ControllerType': str(config['ControllerType'])}
				emit('feature', {'data' : "omniui(%s);" % json.dumps(feature)} )

			@socketio.on('setting_controller', namespace='/websocket')
			def settingControllerRequest(message):
				settings = message.get('data', None)
				if settings is None:
					emit('setting_controller', {'data', 'setting controller error.'} )
					return

				controller_url = settings['controllerURL']
				core_url = settings['coreURL']
				controller_name = settings['controllerName']
				req_body = json.dumps({'coreURL': core_url, 'controllerName': controller_name})

				req = urllib2.Request(controller_url + '/wm/omniui/core')
				req.add_header('Content-Type', 'application/json')
				response = urllib2.urlopen(req, req_body)
				result = response.read()

				emit('setting_controller', {'data' : result})

			# handler other websocket handlers
			@socketio.on('other', namespace='/websocket')
			def topLevelRoute(message):
				url = message.get('url', None)
				req = message.get('request', None)
				result = handleRoute(url, rest=False, req=req)
				if result is None:
					emit('other', {'data' : "Not found: '/%s'" % url })
				else:
					emit('other', {'data' : result} )

			# general top level rest handler
			@app.route('/<url>', methods=['GET', 'POST', 'OPTIONS', 'PUT'])
			@cross_origin()
			def topLevelRoute(url):
				result = handleRoute(url)
				if result is None:
					abort(404, "Not found: '/%s'" % url)
				else:
					return result

			# general second level rest handler
			@app.route('/<prefix>/<suffix>', methods=['GET', 'POST', 'OPTIONS', 'PUT'])
			@cross_origin()
			def secondLevelRoute(prefix, suffix):
				url = prefix + '/' + suffix
				result = handleRoute(url)
				if result is None:
					abort(404, "Not found: '/%s'" % url)
				else:
					return result

			def handleRoute(url, rest=True, req=None):
				if url is None:
					return None
				if url in urlHandlers:
					if rest:
						return urlHandlers[url](request)
					else:
						return urlHandlers[url](req)
				else:
					return None

			app.debug = False
			socketio.run(app, host=handleIP, port=int(handlePort))

	#Register WEBSOCKET and RESTful API
	def registerURLApi(self, requestName, handler):
		urlHandlers[requestName] = handler

	# Register SSE
	def registerSSEHandler(self, sseName, handler):
		sseHandlers[sseName] = handler

	#Register Event
	def registerEventHandler(self,eventName,handler):
		self.eventHandlers.append(EventHandler(eventName,handler))

	def registerEvent(self,eventName,generator,interval):
		thread = Thread(target=self.iterate, args=(eventName,generator,interval))
		self.threads.append(thread)
		thread.start()

	def registerIPC(self, ipcname, handler):
		self.ipcHandlers[ipcname] = handler

	def invokeIPC(self, ipcname, *argument):
		if ipcname in self.ipcHandlers:
			return	self.ipcHandlers[ipcname](*argument)
		else:
			print "invokeIPC fail. %s not found" % ipcname
			return None

	def iterate(self,eventName,generator,interval):
		while True:
			event = generator()
			for handler in self.eventHandlers:
				if(handler.eventName == eventName):
					handler.handler(event)
			time.sleep(interval)

class Watcher:
	def __init__(self):
		self.child = os.fork()
		if self.child ==0 :
			return
		else:
			self.watch()
	def watch(self):
		try:
			os.wait()
		except KeyboardInterrupt:
			print "Ctrl-c received! Sending kill to threads..."
			self.kill()
		sys.exit()
	def kill(self):
		try:
			os.kill(self.child,signal.SIGKILL)
		except OSError: pass

def main():
	Watcher()
	coreInstance = Core()
	coreInstance.start()
	
if __name__ ==	"__main__":
	main()
