#!/usr/bin/python
# coding: UTF-8

from __future__ import print_function, division

import re
import os
import sys
import subprocess
import shlex
import traceback
import requests
import requests.utils
import dbus
import json
import shutil
from time import time
from lxml import html
from urlparse import urljoin, urlparse
from threading import Thread
from contextlib import closing
from urllib import quote

try:
	from Queue import Queue
except ImportError:
	from queue import Queue

RE_PARAM = re.compile(r'\s*(?P<name>[-a-z][-a-z0-9]*)\s*=\s*(:?"(?P<qstr>[^\n\r"]*)"|(?P<str>[^,\s]*))\s*', re.I)
RE_DELIM = re.compile(r'\s*,\s*')
CAPTION = 'Get Video from M3U'
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2403.157 Safari/537.36'
DROP_HEADERS = {'if-none-match', 'if-modified-since', 'accept-encoding', 'upgrade-insecure-requests', 'connection'}

EXT_WITH_ATTRS = {'EXT-X-MEDIA', 'EXT-X-STREAM-INF', 'EXT-X-I-FRAME-STREAM-INF', 'EXT-X-KEY', 'EXT-X-MAP', 'EXT-X-I-FRAME-STREAM-INF'}

def mkquery(**query):
	return '&'.join(quote(k) + '=' + quote(query[k]) for k in query)

def fmt_span(seconds):
	minutes  = seconds // 60
	seconds -= minutes * 60
	hours    = minutes // 60
	minutes -= hours * 60
	return "%02d:%02d:%02d" % (hours, minutes, seconds)

def has_kdialog():
	try:
		p = subprocess.Popen(['kdialog', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		p.stdout.read()
		if p.wait() != 0:
			return False
	except OSError as e:
		return False
	return True

def text_cmd(*cmd):
	p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
	out = p.stdout.read()
	if p.wait() != 0:
		raise KeyboardInterrupt
	if out[-1:] == '\n':
		out = out[:-1]
	return out

def bool_cmd(*cmd):
	p = subprocess.Popen(cmd)
	status = p.wait()
	if status == 0:
		return True
	elif status == 1:
		return False
	else:
		raise KeyboardInterrupt

def check_call_errmsg(cmd, stdout=None):
	p = subprocess.Popen(cmd, stdout=stdout, stderr=subprocess.PIPE)
	if stdout == subprocess.PIPE:
		out = p.stdout.read()
	else:
		out = None
	errmsg = p.stderr.read()
	if p.wait() != 0:
		raise ValueError(errmsg.strip())
	return out

class GUI(object):
	def inputbox(self, msg, init=''):
		raise NotImplementedError

	def warning_yes_no(self, text):
		raise NotImplementedError

	def menu(self, text, items, default=None):
		raise NotImplementedError

	def get_save_filename(self, dirname=None, filter=None):
		raise NotImplementedError

	def passive_popup(self, text, timeout=5):
		raise NotImplementedError

	def show_error(self, text):
		raise NotImplementedError

	def progressbar(self, text, maximum):
		raise NotImplementedError

	def log(self, msg):
		print(msg)

	def __enter__(self):
		return self

	def __exit__(self, ex_type=None, ex_value=None, ex_traceback=None):
		pass

class KDialogGUI(GUI):
	def inputbox(self, msg, init=''):
		return text_cmd('kdialog', '--inputbox', msg, init, '--caption', CAPTION)

	def warning_yes_no(self, text):
		return bool_cmd('kdialog', '--warningyesno', text, '--caption', CAPTION)

	def menu(self, text, items, default=None):
		cmd = ['kdialog', '--menu', text]
		default_item = None

		for tag, item in items:
			cmd.append(tag)
			cmd.append(item)
			if tag == default:
				default_item = item

		if default_item is not None:
			cmd.append('--default')
			cmd.append(default_item)

		cmd.append('--caption')
		cmd.append(CAPTION)

		return text_cmd(*cmd)

	def get_save_filename(self, dirname=None, filter=None):
		if dirname is None:
			dirname = os.getenv("HOME") or os.path.abspath(".")
		cmd = ['kdialog', '--getsavefilename', dirname]
		if filter:
			cmd.append(filter)
		cmd.append('--caption')
		cmd.append(CAPTION)

		while True:
			outfile = text_cmd(*cmd)
			if not os.path.exists(outfile) or \
				self.warning_yes_no('File already exists. Do you want to overwrite it?'):
				return outfile

	def passive_popup(self, text, timeout=5):
		subprocess.check_call(['kdialog', '--passivepopup', text, str(timeout), '--caption', CAPTION])

	def show_error(self, text):
		subprocess.check_call(['kdialog', '--error', text, '--caption', CAPTION])

	def progressbar(self, text, maximum):
		return KDialogProgressBar(text, maximum)

class ProgressBar(object):
	def wasCancelled(self):
		return False

	def setMaximum(self, maximum):
		raise NotImplementedError

	def setValue(self, value):
		raise NotImplementedError

	def setLabelText(self, label):
		raise NotImplementedError

	def __enter__(self):
		return self

	def __exit__(self, ex_type=None, ex_value=None, ex_traceback=None):
		pass

class KDialogProgressBar(ProgressBar):
	def __init__(self, label, maximum):
		bus_name, object_path = text_cmd('kdialog', '--progressbar', text, str(maximum), '--caption', CAPTION).split()
		bus = dbus.SessionBus()
		bar = bus.get_object(bus_name, object_path)
		bar.showCancelButton(True)
		self.bar = bar
		self.props = dbus.Interface(bar, 'org.freedesktop.DBus.Properties')

	def wasCancelled(self):
		return self.bar.wasCancelled()

	def setMaximum(self, maximum):
		self.props.Set('org.kde.kdialog.ProgressDialog', 'maximum', len(maximum))

	def setValue(self, value):
		self.props.Set('org.kde.kdialog.ProgressDialog', 'value', value)

	def setLabelText(self, label):
		self.bar.setLabelText(label)

	def __exit__(self, ex_type=None, ex_value=None, ex_traceback=None):
		self.bar.close()

class TextProgressBar(ProgressBar):
	def __init__(self, label, maximum):
		self._label = label
		self._maximum = maximum
		self._value = 0
		self._barlen = 0
		self._redraw()

	def setMaximum(self, maximum):
		if self._maximum != maximum:
			self._maximum = maximum
			self._recalc_bar()

	def setValue(self, value):
		if self._value != value:
			self._value = value
			self._recalc_bar()

	def _recalc_bar(self):
		if self._value >= self._maximum:
			barlen = 80
		elif self._value > 0:
			barlen = ((80 * (self._value - 1)) // self._maximum)
		else:
			barlen = 0

		if self._barlen != barlen:
			self._barlen = barlen
			self._redraw()

	def setLabelText(self, label):
		if self._label != label:
			self._label = label
			self._redraw()

	def _redraw(self):
		bar = '=' * self._barlen + '>'
		sys.stdout.write('\r%s [%-80s]        ' % (self._label, bar))

	def __exit__(self, ex_type=None, ex_value=None, ex_traceback=None):
		sys.stdout.write('\n')

YES = {'y', 'yes', '1', 'true', 't', 'on'}
NO  = {'n', 'no', '0', 'false', 'f', 'off'}

class TextGUI(GUI):
	def inputbox(self, msg, init=''):
		sys.stdout.write('\x1B[?25h')
		try:
			return raw_input(msg + ' ')
		finally:
			sys.stdout.write('\x1B[?25l')

	def warning_yes_no(self, text):
		while True:
			value = self.inputbox('%s (Y/N):' % text).strip().lower()

			if value in YES:
				return True
			elif value in NO:
				return False

	def menu(self, text, items, default=None):
		while True:
			print(text)
			for index, (tag, item) in enumerate(items):
				s = '%d %s' % (index + 1, item)
				if tag == default:
					s += ' (default)'
				print(s)
			value = self.inputbox('choice (1-%d):').strip()
			if not value and default is not None:
				return default
			try:
				value = int(value, 10) - 1
				if value < 0:
					raise IndexError
				return items[value][0]
			except (IndexError, ValueError):
				pass
			print('')

	def get_save_filename(self, dirname=None, filter=None):
		while True:
			path = self.inputbox('Enter file path:')
			if path:
				return path

	def passive_popup(self, text, timeout=5):
		print(text)

	def show_error(self, text):
		sys.stderr.write('*** Error: %s\n' % text)

	def progressbar(self, text, maximum):
		return TextProgressBar(text, maximum)

	def log(self, msg):
		pass

	def __enter__(self):
		sys.stdout.write('\x1B[?25l')
		return self

	def __exit__(self, ex_type=None, ex_value=None, ex_traceback=None):
		sys.stdout.write('\x1B[?25h')

class Track(object):
	__slots__ = 'url', 'meta'
	def __init__(self, url=None, meta=None):
		self.url  = url
		self.meta = meta or {}

	def label(self):
		res    = self.meta.get('RESOLUTION')
		codecs = self.meta.get('CODECS')
		buf = []

		if res:
			buf.append('%dx%d' % res)

		if codecs:
			buf.append(', '.join(codecs))

		if buf:
			return ', '.join(buf)
		else:
			return self.url

class Playlist(object):
	__solts__ = 'tracks', 'meta'
	def __init__(self):
		self.tracks = []
		self.meta   = {}

def parse_meta(line):
	meta = {}
	if line[:1] == '#':
		line = line[1:]

	parts = line.split(':', 1)

	if len(parts) == 1:
		return line, None

	hdr, params = parts

	if hdr == 'EXTINF':
		params = params.split(',', 1)
		meta = {'DURATION': float(params[0])}
		if len(params) > 1:
			meta['TITLE'] = params[1]
		return hdr, meta

	elif hdr in EXT_WITH_ATTRS:
		parsers = EXT_PARSERS.get(hdr)
		i = 0
		n = len(params)
		while i < n:
			m = RE_PARAM.match(params, i)
			if not m:
				raise SyntaxError("Illegal ext inf in playlist: %s" % line)
			name = m.group('name')
			qval = m.group('qstr')
			val  = m.group('str') or qval
			quoted = qval is not None
			if parsers:
				value = parsers.get(name, lambda val, quoted: val or '')(val, quoted)
			else:
				value = qval or val or ''
			meta[name] = value
			i = m.end()
			if i < n:
				m = RE_DELIM.match(params, i)
				if not m:
					raise SyntaxError("Illegal ext inf in playlist: %s" % line)
				i = m.end()
		return hdr, meta
	else:
		return hdr, params

def track_sort_key(track):
	if 'RESOLUTION' in track.meta:
		width, height = track.meta['RESOLUTION']
		return height, width
	else:
		return 480, 640

EXT_PARSERS = {
	'EXT-X-STREAM-INF': {
		'BANDWIDTH':       lambda val, quoted: int(val, 10),
		'CODECS':          lambda val, quoted: val.split(',') if val else [],
		'RESOLUTION':      lambda val, quoted: tuple(int(px) for px in val.split('x', 1)),
		'CLOSED-CAPTIONS': lambda val, quoted: val if quoted or val != 'NONE' else None
	}
}

def parse_m3u8(data, base_url):
	pl = Playlist()
	lines = data.split("\n")
	if lines:
		if lines[0] == "#EXTM3U":
			it = iter(lines)
			next(it)
			while True:
				try:
					line = next(it)
				except StopIteration:
					break
				else:
					if not line:
						pass
					elif line.startswith('#'):
						hdr, meta = parse_meta(line)
						if hdr in ('EXTINF', 'EXT-X-STREAM-INF'):
							url = next(it)
							track = Track(urljoin(base_url, url))
							track.meta.update(meta)
							track.meta['STREAM'] = hdr == 'EXT-X-STREAM-INF'
							pl.tracks.append(track)
						elif meta is not None:
							pl.meta[hdr] = meta
					else:
						pl.tracks.append(Track(urljoin(base_url, line)))
		else:
			for line in lines:
				if line and not line.startswith('#'):
					pl.tracks.append(Track(urljoin(base_url, line)))
	return pl

def parse_curl(curl):
	headers = {}
	m3u_url = None
	if not curl.startswith('curl '):
		m3u_url = curl
		headers['user-agent'] = USER_AGENT
	else:
		m3u_url = None
		it = iter(shlex.split(curl))
		next(it)
		for arg in it:
			if arg == '-H':
				key, value = next(it).split(':', 1)
				key = key.lower()
				if key not in DROP_HEADERS:
					headers[key] = value.strip()

			elif arg == '--compressed':
				pass

			elif arg.startswith('-'):
				raise ValueError('Cannot parse cURL command line because of unknown argument: '+arg)

			elif m3u_url is not None:
				raise ValueError('Cannot parse cURL command line because it contains more than one url:\n%s\n%s' % (m3u_url, arg))

			else:
				m3u_url = arg

	return m3u_url, headers

def get_video_from_m3u(meta, outfile, gui, thread_count=6):
	try:
		running  = True
		headers  = meta['headers']
		m3u_url  = meta['m3u_url']
		outname  = os.path.split(outfile)[1]
		cachedir = outfile + '.download'
		metaname = os.path.join(cachedir, 'download.json')

		if thread_count < 1:
			raise ValueError('thread_count must be greater than or equal 1')

		with requests.session() as session:
			if 'cookies' in meta:
				session.cookies = requests.utils.cookiejar_from_dict(meta['cookies'])

			with gui.progressbar('Downloading »%s« ETA ---:--:--' % outname, 1) as progress:
				if 'playlist' in meta:
					pl = meta['playlist']
					playlist = Playlist()
					playlist.meta.update(pl['meta'])
					for tr in pl['tracks']:
						track = Track(tr['url'], tr['meta'])
						playlist.tracks.append(track)
				else:
					resp = session.get(m3u_url, headers=headers)
					resp.raise_for_status()
					data = resp.text

					if progress.wasCancelled():
						raise KeyboardInterrupt

					content_type = resp.headers['content-type'].split(";")[0]
					if content_type == 'text/html':
						# it was html, lets try to resolve crappy refresh redirect like t.co uses
						doc = html.fromstring(data)
						meta_el = doc.cssselect("meta[http-equiv='refresh']")
						if meta_el:
							params = {}
							for param in meta_el[0].attrib['content'].split(";")[1:]:
								key, val = param.split('=', 1)
								params[key.lower()] = val

							m3u_url = params['url']
							resp = session.get(m3u_url, headers=headers)
							resp.raise_for_status()
							data = resp.text
							content_type = resp.headers['content-type'].split(";")[0]

							if progress.wasCancelled():
								raise KeyboardInterrupt

					if content_type == 'text/html' and resp.url.startswith('https://www.periscope.tv/'):
						# it was a periscope video page (html) instead
						broadcast_id = urlparse(resp.url).path.split('/')[2]

						resp = session.get('https://api.periscope.tv/api/v2/accessVideoPublic?broadcast_id='+broadcast_id, headers=headers)
						resp.raise_for_status()
						data = json.loads(resp.text)
						m3u_url = data['replay_url']

						if progress.wasCancelled():
							raise KeyboardInterrupt

						resp = session.get(m3u_url, headers=headers)
						resp.raise_for_status()
						data = resp.text
						content_type = resp.headers['content-type'].split(";")[0]

						if progress.wasCancelled():
							raise KeyboardInterrupt

					elif content_type == 'text/html' and resp.url.startswith('https://www.twitch.tv/'):
						# it was a twitch video page (html) instead
						broadcast_id = urlparse(resp.url).path.split('/')[3]

						resp = session.get("https://api.twitch.tv/api/vods/%s/access_token?need_https=false" % broadcast_id, headers=headers)
						resp.raise_for_status()
						data = json.loads(resp.text)

						if progress.wasCancelled():
							raise KeyboardInterrupt

						m3u_url = "https://usher.ttvnw.net/vod/%s.m3u8?%s" % (broadcast_id, mkquery(
							nauth=data['token'],
							nauthsig=data['sig'],
							allow_source='true',
							allow_spectre='true' #,
#							p=??? # TODO: find out where this comes from, but seems to be ignored anyway
						))

						resp = session.get(m3u_url, headers=headers)
						resp.raise_for_status()
						data = resp.text
						content_type = resp.headers['content-type'].split(";")[0]

						if progress.wasCancelled():
							raise KeyboardInterrupt

					if content_type == 'text/html':
						raise Exception("Link points to a webpage, not a m3u playlist.")

					playlist = parse_m3u8(data, m3u_url)

					if any(track.meta['STREAM'] for track in playlist.tracks):
						# it was only a master.m3u8 that points to more streams
						# preselect the highest resolution (or last entry if there is no resolution information):
						tracks = sorted(playlist.tracks, key=track_sort_key)

						if len(tracks) == 1:
							m3u_url = tracks[0].url
						else:
							items = [(track.url, track.label()) for track in playlist.tracks]
							m3u_url = gui.menu('Please choose stream to download:', items, default=tracks[-1].url)

						resp = session.get(m3u_url, headers=headers)
						resp.raise_for_status()
						data = resp.text

						if progress.wasCancelled():
							raise KeyboardInterrupt

						playlist = parse_m3u8(data, m3u_url)

					meta['cookies'] = requests.utils.dict_from_cookiejar(session.cookies)
					meta['playlist'] = {
						'meta':   playlist.meta,
						'tracks': [{'url':track.url, 'meta':track.meta} for track in playlist.tracks]
					}

					if not os.path.exists(cachedir):
						os.mkdir(cachedir)

					with open(metaname, 'wb') as fp:
						json.dump(meta, fp)

				chunk_count = len(playlist.tracks)
				finished_count = 0
				todo = []
				missing_tracks = set()

				for i in range(chunk_count):
					chunkpath = os.path.join(cachedir, '%d.ts' % i)
					if os.path.exists(chunkpath):
						finished_count += 1
					else:
						missing_tracks.add(i)
						todo.append((i, playlist.tracks[i], chunkpath))

				progress.setMaximum(len(missing_tracks))
				start_time = time()
				finished_queue = Queue()
				worker_queues = [Queue() for i in range(thread_count)]

				def worker_func(queue):
					while running:
						item = queue.get()
						try:
							if item is None:
								break
							i, track, chunkpath = item
							dlpath = chunkpath + '.download'
							gui.log('downloading: %s -> %d.ts' % (track.url, i))
							with open(dlpath, 'wb') as fp:
								with closing(session.get(track.url, headers=headers, stream=True)) as resp:
									resp.raise_for_status()
									for data in resp.iter_content(8192):
										fp.write(data)

							if os.path.exists(chunkpath):
								os.unlink(chunkpath)
							os.rename(dlpath, chunkpath)

							finished_queue.put_nowait(i)
						finally:
							queue.task_done()

				workers = []
				for i in range(thread_count):
					thread = Thread(target=worker_func, args=(worker_queues[i],))
					thread.daemon = True
					workers.append(thread)
					thread.start()

				for i, item in enumerate(todo):
					worker_queues[i % thread_count].put_nowait(item)

				# signal end
				for queue in worker_queues:
					queue.put_nowait(None)

				while running:
					tracknr = finished_queue.get()
					missing_tracks.remove(tracknr)
					if not missing_tracks:
						running = False

					dl_count = len(todo) - len(missing_tracks)
					elapsed  = time() - start_time
					avgtime  = elapsed / dl_count
					esttime  = avgtime * len(todo)
					remtime  = esttime - elapsed

					progress.setValue(dl_count)
					progress.setLabelText('Downloading »%s« ETA -%s' % (outname, fmt_span(remtime)))
					if progress.wasCancelled():
						raise KeyboardInterrupt

				progress.setMaximum(len(playlist.tracks))
				progress.setValue(0)
				progress.setLabelText('Assembling »%s« 0/%d' % (outname, len(playlist.tracks)))
				with open(outfile, 'wb') as fp:
					for i in range(len(playlist.tracks)):
						progress.setValue(i + 1)
						progress.setLabelText('Assembling »%s« %d/%d' % (outname, i+1, len(playlist.tracks)))
						chunkpath = os.path.join(cachedir, '%d.ts' % i)
						with open(chunkpath, 'rb') as chunkfp:
							chunk = chunkfp.read()
						fp.write(chunk)
						if progress.wasCancelled():
							raise KeyboardInterrupt

				shutil.rmtree(cachedir)

		gui.passive_popup('Finished saving video: '+outfile)

	except KeyboardInterrupt:
		gui.log("\ndownload canceled by user")
		if outfile is not None:
			gui.passive_popup('Download canceled by user: '+outfile)

def main(args):
	use_gui = None
	while args:
		if args[0] == '--gui':
			use_gui = True
			del args[0]
		elif args[0] == '--no-gui':
			use_gui = False
			del args[0]
		else:
			break

	if use_gui is None:
		use_gui = has_kdialog()

	with (KDialogGUI() if use_gui else TextGUI()) as gui:
		try:
			if len(args) < 1:
				outfile = gui.get_save_filename(filter='*.ts')
			else:
				outfile = sys.argv[1]

			cachedir = outfile + '.download'
			metaname = os.path.join(cachedir, 'download.json')
			meta = None

			if os.path.exists(metaname):
				if gui.warning_yes_no('Continue in progress download?'):
					with open(metaname, 'rb') as fp:
						meta = json.load(fp)

			if meta is None:
				if len(args) < 2:
					curl = gui.inputbox('Paste M3U URL/cURL from network tab:')
				else:
					curl = ' '.join(sys.argv[2:])
				m3u_url, headers = parse_curl(curl)
				meta = {'headers': headers, 'm3u_url': m3u_url}

			get_video_from_m3u(meta, outfile, gui)

		except Exception as e:
			traceback.print_exc()
			gui.show_error(str(e))

if __name__ == '__main__':
	import sys
	main(sys.argv[1:])
