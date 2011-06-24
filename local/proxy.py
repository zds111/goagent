#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Based on GAppProxy 2.0.0 by Du XiaoGang <dugang@188.com>
# Based on WallProxy 0.4.0 by hexieshe <www.ehust@gmail.com>

__version__ = 'beta'
__author__ =  'phus.lu@gmail.com'

import sys, os, re, time
import errno, zlib, struct, binascii
import logging
import httplib, urllib2, urlparse, socket, select
import BaseHTTPServer, SocketServer
import random
import ConfigParser
import ssl
import ctypes
import threading, Queue
try:
    import OpenSSL
except ImportError:
    OpenSSL = None

class Common(object):
    '''global config module, based on GappProxy 2.0.0'''
    FILENAME = sys.argv[1] if len(sys.argv) == 2 and os.path.isfile(os.sys.argv[1]) else os.path.splitext(__file__)[0] + '.ini'
    ConfigParser.RawConfigParser.OPTCRE = re.compile(r'(?P<option>[^=\s][^=]*)\s*(?P<vi>[=])\s*(?P<value>.*)$')

    def __init__(self):
        '''read config from proxy.ini'''
        self.config = ConfigParser.ConfigParser()
        self.config.read(self.__class__.FILENAME)
        self.GAE_APPIDS    = self.config.get('gae', 'appid').replace('.appspot.com', '').split('|')
        self.GAE_PASSWORD  = self.config.get('gae', 'password').strip()
        self.GAE_PREFER    = self.config.get('gae', 'prefer')
        self.GAE_IP        = self.config.get('gae', 'ip')
        self.GAE_PORT      = self.config.getint('gae', 'port')
        self.GAE_VISIBLE   = self.config.getint('gae', 'visible')
        self.GAE_DEBUG     = self.config.get('gae', 'debug')
        self.GAE_FORCEHTTPS= set(self.config.get('gae', 'forcehttps').split('|'))
        self.GAE_PATH      = self.config.get('gae', 'path')
        self.GAE_BINDHOSTS = dict((host, self.GAE_APPIDS[0]) for host in self.config.get('gae', 'bindhosts').split('|')) if self.config.has_option('gae', 'bindhosts') else {}
        self.GAE_CERTS     = self.config.get('gae', 'certs').split('|')

        self.PROXY_ENABLE   = self.config.getint('proxy', 'enable')
        self.PROXY_TYPE     = self.config.get('proxy', 'type')
        self.PROXY_HOST     = self.config.get('proxy', 'host')
        self.PROXY_PORT     = self.config.getint('proxy', 'port')
        self.PROXY_USERNAME = self.config.get('proxy', 'username')
        self.PROXY_PASSWROD = self.config.get('proxy', 'password')

        self.HTTP_HOSTSLIST  = [x.split('|') for x in self.config.get('http', 'hosts').split('||')]
        self.HTTP_TIMEOUT    = self.config.getint('http', 'timeout')
        self.HTTP_SAMPLE     = self.config.getint('http', 'sample')
        self.HTTPS_HOSTSLIST = [x.split('|') for x in self.config.get('https', 'hosts').split('||')]
        self.HTTPS_TIMEOUT   = self.config.getint('https', 'timeout')
        self.HTTPS_SAMPLE    = self.config.getint('https', 'sample')

        self.HOSTS           = self.config.items('hosts')
        logging.basicConfig(level=getattr(logging, self.GAE_DEBUG), format='%(levelname)s - - %(asctime)s %(message)s', datefmt='[%d/%b/%Y %H:%M:%S]')

    def select_appid(self, url):
        appid = None
        if len(self.GAE_APPIDS) == 1:
            return self.GAE_APPIDS[0]
        if self.GAE_BINDHOSTS:
            appid = self.GAE_BINDHOSTS.get(urlparse.urlsplit(url)[1])
        appid = appid or random.choice(self.GAE_APPIDS)
        return appid

    def resolve_host(self, host):
        if host.endswith('.appspot.com'):
            if self.GAE_PREFER == 'http':
                return self.HTTP_HOSTSLIST
            else:
                return self.HTTPS_HOSTSLIST
        for pat, iplist in self.HOSTS:
            if host.endswith(pat):
                if iplist:
                    return [x.split('|') for x in iplist.split('||')]
                else:
                    return ''
        else:
            return None

    def info(self):
        info = ''
        info += '--------------------------------------------\n'
        info += 'OpenSSL Module : %s\n' % 'Enabled' if OpenSSL else 'Disabled'
        info += 'Listen Address : %s:%d\n' % (self.GAE_IP, self.GAE_PORT)
        info += 'Log Level      : %s\n' % self.GAE_DEBUG
        info += 'Local Proxy    : %s://%s:%s\n' % (self.PROXY_TYPE, self.PROXY_HOST, self.PROXY_PORT) if self.PROXY_ENABLE else ''
        info += 'GAE Mode       : %s\n' % self.GAE_PREFER
        info += 'GAE APPID      : %s\n' % '|'.join(self.GAE_APPIDS)
        info += 'GAE BindHost   : %s\n' % '|'.join('%s=%s' % (k, v) for k, v in self.GAE_BINDHOSTS.items()) if self.GAE_BINDHOSTS else ''
        info += '--------------------------------------------\n'
        return info

if __name__ == '__main__':
    common = Common()

class MultiplexConnection(object):
    '''multiplex tcp connection class'''
    def __init__(self, hostslist, port, timeout, sample, proxy=None):
        self.socket = None
        self._sockets = set([])
        if proxy:
            self.connect_proxy(hostslist, port, timeout, sample, proxy)
        else:
            self.connect(hostslist, port, timeout, sample)
    def connect(self, hostslist, port, timeout, sample):
        for i, hosts in enumerate(hostslist):
            if len(hosts) > sample:
                hosts = random.sample(hosts, sample)
            logging.debug('MultiplexConnection connect (%s, %s)', hosts, port)
            socs = []
            for host in hosts:
                soc_family = socket.AF_INET6 if ':' in host else socket.AF_INET
                soc = socket.socket(soc_family, socket.SOCK_STREAM)
                soc.setblocking(0)
                logging.debug('MultiplexConnection connect_ex (%r, %r)', host, port)
                err = soc.connect_ex((host, port))
                self._sockets.add(soc)
                socs.append(soc)
            (_, outs, _) = select.select([], socs, [], timeout)
            if outs:
                self.socket = outs[0]
                self.socket.setblocking(1)
                self._sockets.remove(self.socket)
                if i > 0:
                    hostslist[:i], hostslist[i:] = hostslist[i:], hostslist[:i]
                break
            else:
                logging.warning('MultiplexConnection Cannot hosts %r:%r', hosts, port)
        else:
            raise RuntimeError(r'MultiplexConnection Cannot Connect to hostslist %s:%s' % (hostslist, port))
    def connect_proxy(self, hostslist, port, timeout, sample, (proxy_type, proxy_host, proxy_port, proxy_username, proxy_password)):
        for i, hosts in enumerate(hostslist):
            if len(hosts) > sample:
                hosts = random.sample(hosts, sample)
            logging.debug('MultiplexConnection connect_proxy (%s, %s)', hosts, port)
            socs = []
            for host in hosts:
                soc_family = socket.AF_INET6 if ':' in proxy_host else socket.AF_INET
                soc = socket.socket(soc_family, socket.SOCK_STREAM)
                logging.debug('MultiplexConnection connect_proxy (%r, %r)', host, port)
                try:
                    soc.settimeout(timeout)
                    soc.connect((host, port))
                    soc.send('CONNECT %s:%d HTTP/1.1\r\n\r\n', host, port)
                    #TODO
                except:
                    pass
            else:
                logging.warning('MultiplexConnection Cannot hosts %r:%r', hosts, port)
        else:
            raise RuntimeError(r'MultiplexConnection Cannot Connect to hostslist %s:%s' % (hostslist, port))
    def close(self):
        for soc in self._sockets:
            try:
                soc.close()
            except:
                pass

_socket_create_connection = socket.create_connection
def socket_create_connection(address, timeout=10, source_address=None):
    host, port = address
    logging.debug('socket_create_connection connect (%r, %r)', host, port)
    hostslist = common.resolve_host(host)
    if hostslist is not None:
        msg = "socket_create_connection returns an empty list"
        try:
            hostslist = hostslist or [[x[-1][0] for x in socket.getaddrinfo(host, 80)]]
            if common.GAE_PREFER == 'http':
                timeout, sample = common.HTTP_TIMEOUT, common.HTTP_SAMPLE
            else:
                timeout, sample = common.HTTPS_TIMEOUT, common.HTTPS_SAMPLE
            logging.debug("socket_create_connection connect hostslist: (%r, %r)", hostslist, port)
            if common.PROXY_ENABLE:
                proxy = (common.PROXY_TYPE, common.PROXY_HOST, common.PROXY_PORT, common.PROXY_USERNAME, common.PROXY_PASSWROD)
                conn = MultiplexConnection(hostslist, port, timeout, sample, proxy=proxy)
            else:
                conn = MultiplexConnection(hostslist, port, timeout, sample)
            conn.close()
            soc = conn.socket
            soc.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, True)
            return soc
        except socket.error, msg:
            logging.error('socket_create_connection connect fail: (%r, %r)', hostslist, port)
            soc = None
        if not soc:
            raise socket.error, msg
    else:
        return _socket_create_connection(address, timeout)
socket.create_connection = socket_create_connection

_httplib_HTTPConnection_putrequest = httplib.HTTPConnection.putrequest
def httplib_HTTPConnection_putrequest(self, method, url, skip_host=0, skip_accept_encoding=1):
    return _httplib_HTTPConnection_putrequest(self, method, url, skip_host, skip_accept_encoding)
httplib.HTTPConnection.putrequest = httplib_HTTPConnection_putrequest

def socket_forward(local, remote, timeout=60, tick=2, maxping=None, maxpong=None):
    count = timeout // tick
    try:
        while 1:
            count -= 1
            (ins, _, errors) = select.select([local, remote], [], [local, remote], tick)
            if errors:
                break
            if ins:
                for soc in ins:
                    data = soc.recv(8192)
                    if data:
                        if soc is local:
                            remote.send(data)
                            count = maxping or timeout // tick
                        else:
                            local.send(data)
                            count = maxpong or timeout // tick
            if count == 0:
                break
    except Exception, ex:
        logging.warning('socket_forward error=%s', ex)
        raise

class RootCA(object):
    '''RootCA module, based on WallProxy 0.4.0'''

    CA = None
    CALock = threading.Lock()

    @staticmethod
    def readFile(filename):
        try:
            f = open(filename, 'rb')
            c = f.read()
            f.close()
            return c
        except IOError:
            return None

    @staticmethod
    def writeFile(filename, content):
        f = open(filename, 'wb')
        f.write(str(content))
        f.close()

    @staticmethod
    def createKeyPair(type=None, bits=1024):
        if type is None:
            type = OpenSSL.crypto.TYPE_RSA
        pkey = OpenSSL.crypto.PKey()
        pkey.generate_key(type, bits)
        return pkey

    @staticmethod
    def createCertRequest(pkey, digest='sha1', **subj):
        req = OpenSSL.crypto.X509Req()
        subject = req.get_subject()
        for k,v in subj.iteritems():
            setattr(subject, k, v)
        req.set_pubkey(pkey)
        req.sign(pkey, digest)
        return req

    @staticmethod
    def createCertificate(req, (issuerKey, issuerCert), serial, (notBefore, notAfter), digest='sha1'):
        cert = OpenSSL.crypto.X509()
        cert.set_serial_number(serial)
        cert.gmtime_adj_notBefore(notBefore)
        cert.gmtime_adj_notAfter(notAfter)
        cert.set_issuer(issuerCert.get_subject())
        cert.set_subject(req.get_subject())
        cert.set_pubkey(req.get_pubkey())
        cert.sign(issuerKey, digest)
        return cert

    @staticmethod
    def loadPEM(pem, type):
        handlers = ('load_privatekey', 'load_certificate_request', 'load_certificate')
        return getattr(OpenSSL.crypto, handlers[type])(OpenSSL.crypto.FILETYPE_PEM, pem)

    @staticmethod
    def dumpPEM(obj, type):
        handlers = ('dump_privatekey', 'dump_certificate_request', 'dump_certificate')
        return getattr(OpenSSL.crypto, handlers[type])(OpenSSL.crypto.FILETYPE_PEM, obj)

    @staticmethod
    def makeCA():
        pkey = RootCA.createKeyPair(bits=2048)
        subj = {'countryName': 'CN', 'stateOrProvinceName': 'Internet',
                'localityName': 'Cernet', 'organizationName': 'GoAgent',
                'organizationalUnitName': 'GoAgent Root', 'commonName': 'GoAgent CA'}
        req = RootCA.createCertRequest(pkey, **subj)
        cert = RootCA.createCertificate(req, (pkey, req), 0, (0, 60*60*24*7305))  #20 years
        return (RootCA.dumpPEM(pkey, 0), RootCA.dumpPEM(cert, 2))

    @staticmethod
    def makeCert(host, (cakey, cacrt), serial):
        pkey = RootCA.createKeyPair()
        subj = {'countryName': 'CN', 'stateOrProvinceName': 'Internet',
                'localityName': 'Cernet', 'organizationName': host,
                'organizationalUnitName': 'GoAgent Branch', 'commonName': host}
        req = RootCA.createCertRequest(pkey, **subj)
        cert = RootCA.createCertificate(req, (cakey, cacrt), serial, (0, 60*60*24*7305))
        return (RootCA.dumpPEM(pkey, 0), RootCA.dumpPEM(cert, 2))

    @staticmethod
    def getCertificate(host):
        basedir = os.path.dirname(__file__)
        keyFile = os.path.join(basedir, 'certs/%s.key' % host)
        crtFile = os.path.join(basedir, 'certs/%s.crt' % host)
        if os.path.exists(keyFile):
            return (keyFile, crtFile)
        if not OpenSSL:
            keyFile = os.path.join(basedir, 'CA.key')
            crtFile = os.path.join(basedir, 'CA.crt')
            return (keyFile, crtFile)
        if not os.path.isfile(keyFile):
            with RootCA.CALock:
                if not os.path.isfile(keyFile):
                    logging.info('RootCA getCertificate for %r', host)
                    serialFile = os.path.join(basedir, 'CA.srl')
                    SERIAL = RootCA.readFile(serialFile)
                    SERIAL = int(SERIAL)+1
                    key, crt = RootCA.makeCert(host, RootCA.CA, SERIAL)
                    RootCA.writeFile(keyFile, key)
                    RootCA.writeFile(crtFile, crt)
                    RootCA.writeFile(serialFile, str(SERIAL))
        return (keyFile, crtFile)

    @staticmethod
    def checkCA():
        #Check CA imported
        if os.name == 'nt':
            basedir = os.path.dirname(__file__)
            os.environ['PATH'] += os.pathsep + basedir
            #cmd = r'certmgr.exe -add "%s\CA.crt" -c -s -r localMachine Root >NUL' % basedir
            cmd = r'certutil.exe -store Root "GoAgent CA" >NUL || certutil.exe -f -addstore root CA.crt'
            if os.system(cmd) != 0:
                logging.warn('Import GoAgent CA \'CA.crt\' %r failed.', cmd)
        #Check CA file
        cakeyFile = os.path.join(os.path.dirname(__file__), 'CA.key')
        cacrtFile = os.path.join(os.path.dirname(__file__), 'CA.crt')
        cakey = RootCA.readFile(cakeyFile)
        cacrt = RootCA.readFile(cacrtFile)
        if OpenSSL:
            RootCA.CA = (RootCA.loadPEM(cakey, 0), RootCA.loadPEM(cacrt, 2))
            for host in common.GAE_CERTS:
                RootCA.getCertificate(host)

def gae_encode_data(dic):
    from binascii import b2a_hex
    return '&'.join('%s=%s' % (k, b2a_hex(str(v))) for k, v in dic.iteritems())

def gae_decode_data(qs):
    from binascii import a2b_hex
    return dict((k, a2b_hex(v)) for k, v in (x.split('=') for x in qs.split('&')))

class GaeProxyHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    partSize = 1024000
    fetchTimeout = 5
    FR_Headers = ('', 'host', 'vary', 'via', 'x-forwarded-for', 'proxy-authorization', 'proxy-connection', 'upgrade', 'keep-alive')
    opener = None
    opener_lock = threading.Lock()

    def address_string(self):
        return '%s:%s' % self.client_address[:2]

    def send_response(self, code, message=None):
        self.log_request(code)
        if message is None:
            if code in self.responses:
                message = self.responses[code][0]
            else:
                message = 'GoAgent Notify'
        if self.request_version != 'HTTP/0.9':
            self.wfile.write('%s %d %s\r\n' % (self.protocol_version, code, message))

    def end_error(self, code, message=None, data=None):
        if not data:
            self.send_error(code, message)
        else:
            self.send_response(code, message)
            self.wfile.write(data)
        self.connection.close()

    def finish(self):
        try:
            self.wfile.close()
            self.rfile.close()
        except socket.error, (err, _):
            # Connection closed by browser
            if err == 10053 or err == errno.EPIPE:
                self.log_message('socket.error: [%s] "Software caused connection abort"', err)
            else:
                raise

    def _opener(self):
        '''double-checked locking url opener'''
        if self.opener is None:
            with GaeProxyHandler.opener_lock:
                if self.opener is None:
                    self.opener = urllib2.build_opener(urllib2.ProxyHandler({}))
                    self.opener.addheaders = []
        return self.opener

    def _fetch(self, url, method, headers, payload):
        errors = []
        params = {'url':url, 'method':method, 'headers':str(headers), 'payload':payload}
        logging.debug('GaeProxyHandler fetch params %s', params)
        if common.GAE_PASSWORD:
            params['password'] = common.GAE_PASSWORD
        params = gae_encode_data(params)
        for i in range(1, 4):
            try:
                appid = common.select_appid(url)
                fetchserver = '%s://%s.appspot.com%s' % (common.GAE_PREFER, appid, common.GAE_PATH)
                logging.debug('GaeProxyHandler fetch %r from %r', url, fetchserver)
                request = urllib2.Request(fetchserver, zlib.compress(params, 9))
                request.add_header('Content-Type', 'application/octet-stream')
                response = self._opener().open(request)
                data = response.read()
                response.close()
            except urllib2.HTTPError, e:
                # www.google.cn:80 is down, switch to https
                if e.code == 502 or e.code == 504:
                    common.GAE_PREFER = 'https'
                    sys.stdout.write(common.info())
                errors.append('%d: %s' % (e.code, httplib.responses.get(e.code, 'Unknown HTTPError')))
                continue
            except urllib2.URLError, e:
                if e.reason[0] in (11004, 10051, 10054, 10060, 'timed out'):
                    # it seems that google.cn is reseted, switch to https
                    if e.reason[0] == 10054:
                        common.GAE_PREFER = 'https'
                        sys.stdout.write(common.info())
                errors.append(str(e))
                continue
            except Exception, e:
                errors.append(repr(e))
                continue

            try:
                if data[0] == '0':
                    raw_data = data[1:]
                elif data[0] == '1':
                    raw_data = zlib.decompress(data[1:])
                else:
                    raise ValueError('Data format not match(%s)' % url)
                data = {}
                data['code'], hlen, clen = struct.unpack('>3I', raw_data[:12])
                if len(raw_data) != 12+hlen+clen:
                    raise ValueError('Data length not match')
                data['content'] = raw_data[12+hlen:]
                if data['code'] == 555:     #Urlfetch Failed
                    raise ValueError(data['content'])
                data['headers'] = gae_decode_data(raw_data[12:12+hlen])
                return (0, data)
            except Exception, e:
                errors.append(str(e))
        return (-1, errors)

    def _RangeFetch(self, m, data):
        m = map(int, m.groups())
        start = m[0]
        end = m[2] - 1
        if 'range' in self.headers:
            req_range = re.search(r'(\d+)?-(\d+)?', self.headers['range'])
            if req_range:
                req_range = [u and int(u) for u in req_range.groups()]
                if req_range[0] is None:
                    if req_range[1] is not None:
                        if m[1]-m[0]+1==req_range[1] and m[1]+1==m[2]:
                            return False
                        if m[2] >= req_range[1]:
                            start = m[2] - req_range[1]
                else:
                    start = req_range[0]
                    if req_range[1] is not None:
                        if m[0]==req_range[0] and m[1]==req_range[1]:
                            return False
                        if end > req_range[1]:
                            end = req_range[1]
            data['headers']['content-range'] = 'bytes %d-%d/%d' % (start, end, m[2])
        elif start == 0:
            data['code'] = 200
            del data['headers']['content-range']
        data['headers']['content-length'] = end-start+1
        partSize = self.__class__.partSize
        self.send_response(data['code'])
        for k,v in data['headers'].iteritems():
            self.send_header(k.title(), v)
        self.end_headers()
        if start == m[0]:
            self.wfile.write(data['content'])
            start = m[1] + 1
            partSize = len(data['content'])
        failed = 0
        logging.info('>>>>>>>>>>>>>>> Range Fetch started')
        while start <= end:
            self.headers['Range'] = 'bytes=%d-%d' % (start, start + partSize - 1)
            retval, data = self._fetch(self.path, self.command, self.headers, '')
            if retval != 0:
                time.sleep(4)
                continue
            m = re.search(r'bytes\s+(\d+)-(\d+)/(\d+)', data['headers'].get('content-range',''))
            if not m or int(m.group(1))!=start:
                if failed >= 1:
                    break
                failed += 1
                continue
            start = int(m.group(2)) + 1
            logging.info('>>>>>>>>>>>>>>> %s %d' % (data['headers']['content-range'], end))
            failed = 0
            self.wfile.write(data['content'])
        logging.info('>>>>>>>>>>>>>>> Range Fetch ended')
        self.connection.close()
        return True

    def resolve_netloc(self, netloc, defaultport=80):
        if netloc.rfind(':') > netloc.rfind(']'):
            host, _, port = netloc.rpartition(':')
            port = int(port)
        else:
            host = netloc
            port = defaultport
        if host[0] == '[':
            host = host.strip('[]')
        return host, port

    def do_CONNECT(self):
        host, port = self.resolve_netloc(self.path, 443)
        hostslist = common.resolve_host(host)
        if hostslist is not None:
            return self.do_CONNECT_Direct()
        else:
            return self.do_CONNECT_Forward()

    def do_CONNECT_Direct(self):
        try:
            logging.debug('GaeProxyHandler.do_CONNECT_Directt %s' % self.path)
            host, port = self.resolve_netloc(self.path, 443)
            soc = socket.create_connection((host, port))
            self.log_request(200)
            self.wfile.write('%s 200 Connection established\r\n' % self.protocol_version)
            self.wfile.write('Proxy-agent: %s\r\n\r\n' % self.version_string())
            socket_forward(self.connection, soc, maxping=8)
        except:
            logging.exception('GaeProxyHandler.do_CONNECT_Direct Error')
            self.send_error(502, 'GaeProxyHandler.do_CONNECT_Direct Error')
        finally:
            try:
                self.connection.close()
            except:
                pass
            try:
                soc.close()
            except:
                pass

    def do_CONNECT_Forward(self):
        # for ssl proxy
        host, port = self.resolve_netloc(self.path, 443)
        keyFile, crtFile = RootCA.getCertificate(host)
        self.send_response(200)
        self.end_headers()
        try:
            ssl_sock = ssl.SSLSocket(self.connection, keyFile, crtFile, True)
        except ssl.SSLError, e:
            logging.exception('SSLError: %s', e)
            return

        # rewrite request line, url to abs
        first_line = ''
        while True:
            data = ssl_sock.read()
            # EOF?
            if data == '':
                # bad request
                ssl_sock.close()
                self.connection.close()
                return
            # newline(\r\n)?
            first_line += data
            if '\n' in first_line:
                first_line, data = first_line.split('\n', 1)
                first_line = first_line.rstrip('\r')
                break
        # got path, rewrite
        method, path, ver = first_line.split()
        if path.startswith('/'):
            path = 'https://%s%s' % (host if port=='443' else self.path, path)
        # connect to local proxy server
        localhost = {'0.0.0.0':'127.0.0.1','::':'::1'}.get(common.GAE_IP, common.GAE_IP)
        localport = common.GAE_PORT
        soc = socket.socket(LocalProxyServer.address_family, socket.SOCK_STREAM)
        soc.connect((localhost, localport))
        soc.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32*1024)
        soc.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32*1024)
        soc.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        soc.send('%s %s %s\r\n%s' % (method, path, ver, data))

        # forward https request
        ssl_sock.settimeout(1)
        while True:
            try:
                data = ssl_sock.read(8192)
            except ssl.SSLError, e:
                if str(e).lower().find('timed out') == -1:
                    # error
                    soc.close()
                    ssl_sock.close()
                    self.connection.close()
                    return
                # timeout
                break
            if data != '':
                soc.send(data)
            else:
                # EOF
                break

        ssl_sock.setblocking(True)
        # simply forward response
        while True:
            data = soc.recv(8192)
            if data != '':
                try:
                    ssl_sock.write(data)
                except socket.error, (err, _):
                    if err == 10053 or err == errno.EPIPE:
                        self.log_message('socket.error: [%s] Software caused connection abort', err)
                    else:
                        raise
            else:
                # EOF
                break
        # clean
        soc.close()
        ssl_sock.shutdown(socket.SHUT_WR)
        ssl_sock.close()
        self.connection.close()

    def do_METHOD(self):
        netloc = urlparse.urlsplit(self.path, 'http')[1]
        host, port = self.resolve_netloc(netloc)
        hostslist = common.resolve_host(host)
        if hostslist is None:
            return self.do_METHOD_Forward()
        else:
            return self.do_METHOD_Direct()

    def do_METHOD_Direct(self):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(self.path, 'http')
        host, port = self.resolve_netloc(netloc)
        if host in common.GAE_FORCEHTTPS:
            self.send_response(301)
            self.send_header("Location", self.path.replace('http://', 'https://'))
            self.end_headers()
            return
        try:
            soc = socket.create_connection((host, port))
            data = '%s %s %s\r\n'  % (self.command, urlparse.urlunparse(('', '', path, params, query, '')), self.request_version)
            data += ''.join('%s: %s\r\n' % (k, self.headers[k]) for k in self.headers if not k.lower().startswith('proxy-'))
            data += 'Connection: close\r\n'
            data += '\r\n'
            content_length = int(self.headers.get('content-length', 0))
            if content_length > 0:
                data += self.rfile.read(content_length)
            soc.send(data)
            socket_forward(self.connection, soc, maxping=10)
        except Exception, ex:
            logging.exception('SimpleProxyHandler.do_GET Error, %s', ex)
            self.send_error(502, 'SimpleProxyHandler.do_GET Error (%s)' % ex)
        finally:
            try:
                self.connection.close()
            except:
                pass
            try:
                soc.close()
            except:
                pass

    def do_METHOD_Forward(self):
        if self.path.startswith('/'):
            host = self.headers['host']
            if host.endswith(':80'):
                host = host[:-3]
            self.path = 'http://%s%s' % (host , self.path)

        payload_len = int(self.headers.get('content-length', 0))
        if payload_len > 0:
            payload = self.rfile.read(payload_len)
        else:
            payload = ''

        for k in self.__class__.FR_Headers:
            try:
                del self.headers[k]
            except KeyError:
                pass

        retval, data = self._fetch(self.path, self.command, self.headers, payload)
        try:
            if retval == -1:
                return self.end_error(502, str(data))
            if data['code']==206 and self.command=='GET':
                m = re.search(r'bytes\s+(\d+)-(\d+)/(\d+)', data['headers'].get('content-range',''))
                if m and self._RangeFetch(m, data):
                    return
            self.send_response(data['code'])
            for k,v in data['headers'].iteritems():
                self.send_header(k.title(), v)
            self.end_headers()
            self.wfile.write(data['content'])
        except socket.error, (err, _):
            # Connection closed before proxy return
            if err == errno.EPIPE or err == 10053:
                return
        self.connection.close()

    do_GET = do_METHOD
    do_POST = do_METHOD
    do_PUT = do_METHOD
    do_DELETE = do_METHOD

class LocalProxyServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == '__main__':
    RootCA.checkCA()
    sys.stdout.write(common.info())
    if os.name == 'nt' and not common.GAE_VISIBLE:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    SocketServer.TCPServer.address_family = {True:socket.AF_INET6, False:socket.AF_INET}[':' in common.GAE_IP]
    httpd = LocalProxyServer((common.GAE_IP, common.GAE_PORT), GaeProxyHandler)
    httpd.serve_forever()
