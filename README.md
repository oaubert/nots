NoSQL Trace Storage
===================

NoSQL-based trace storage system. This server is able to store
real-time traces of multiple clients using interactive
applications. It is intended to be used as backend for the ktbs4js
trace capture client API.

How To 
-------

### Setup it

You need to install : 
* mongo DB     (http://www.mongodb.org/) [ on Debian/Ubuntu: mongodb-server ]
* python pymongo module                  [ on Debian/Ubuntu: python-pymongo ]
* python 2.7   (http://python.org/)
* flask        (http://flask.pocoo.org/) [ on Debian/Ubuntu: python-flask ]

### Run it

* If MongoDB is not started, start it with the command: "mongod" (or see you distribution informations about services)
* Start the Server with the commmand : "python jstraceserver.py"
* Grab a copy of http://github.com/oaubert/ktbs4js/ into static/src/
* To test it, open your browser and go to : (http://127.0.0.1:5000/static/test.html)
