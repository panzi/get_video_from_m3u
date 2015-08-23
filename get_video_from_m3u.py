#!/usr/bin/python
# coding: UTF-8

from __future__ import print_function, division

import re
import os
import subprocess
import shlex
import traceback
import requests
import dbus
from time import time
from lxml import html
from urlparse import urljoin, urlparse
from contextlib import closing, contextmanager

RE_EXT = re.compile('\.[a-z]*$',re.I)
CAPTION = 'Get Video from M3U'
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2403.157 Safari/537.36'
DROP_HEADERS = {'if-none-match', 'if-modified-since', 'accept-encoding', 'upgrade-insecure-requests', 'connection'}

def fmt_span(seconds):
	minutes  = seconds // 60
	seconds -= minutes * 60
	hours    = minutes // 60
	minutes -= hours * 60
	return "%02d:%02d:%02d" % (hours, minutes, seconds)

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

def check_call_errmsg(cmd,stdout=None):
	p = subprocess.Popen(cmd,stdout=stdout,stderr=subprocess.PIPE)
	if stdout == subprocess.PIPE:
		out = p.stdout.read()
	else:
		out = None
	errmsg = p.stderr.read()
	if p.wait() != 0:
		raise ValueError(errmsg.strip())
	return out

def inputbox(msg,init=''):
	return text_cmd('kdialog','--inputbox',msg,init,'--caption',CAPTION)

def warning_yes_no(text):
	return bool_cmd('kdialog','--warningyesno',text,'--caption',CAPTION)

def get_save_filename(dirname=None,filter=None):
	if dirname is None:
		dirname = os.getenv("HOME") or os.path.abspath(".")
	cmd = ['kdialog','--getsavefilename',dirname]
	if filter:
		cmd.append(filter)
	cmd.append('--caption')
	cmd.append(CAPTION)

	while True:
		outfile = text_cmd(*cmd)
		if not os.path.exists(outfile) or \
			warning_yes_no('File already exists. Do you want to overwrite it?'):
			return outfile

def passive_popup(text,timeout=5):
	subprocess.check_call(['kdialog','--passivepopup',text,str(timeout),'--caption',CAPTION])

def show_error(text):
	subprocess.check_call(['kdialog','--error',text,'--caption',CAPTION])

@contextmanager
def progressbar(text,maximum):
	bus_name, object_path = text_cmd('kdialog','--progressbar',text,str(maximum),'--caption',CAPTION).split()
	bus = dbus.SessionBus()
	bar = bus.get_object(bus_name, object_path)
	try:
		yield bar
	finally:
		bar.close()

def get_video_from_m3u(curl=None,outfile=None):
	try:
		if curl is None:
			curl = inputbox('Paste M3U URL/cURL from network tab:')
		headers = {}
		if not curl.startswith('curl '):
			m3u_url = curl
			headers['user-agent'] = USER_AGENT
		else:
			m3u_url = None
			it = iter(shlex.split(curl))
			next(it)
			for arg in it:
				if arg == '-H':
					key, value = next(it).split(':',1)
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

		if outfile is None:
			outfile = get_save_filename(filter='*.ts')

		outname = os.path.split(outfile)[1]

		with requests.session() as session:
			with progressbar('Downloading »%s« ETA ---:--:--' % outname,1) as progress:
				progress.showCancelButton(True)

				resp = session.get(m3u_url, headers=headers)
				resp.raise_for_status()
				data = resp.text

				if progress.wasCancelled():
					raise KeyboardInterrupt

				content_type = resp.headers['content-type'].split(";")[0]
				if content_type == 'text/html':
					# it was html, lets try to resolve crappy refresh redirect like t.co uses
					doc = html.fromstring(data)
					meta = doc.cssselect("meta[http-equiv='refresh']")
					if meta:
						params = {}
						for param in meta[0].attrib['content'].split(";")[1:]:
							key, val = param.split('=',1)
							params[key.lower()] = val

						m3u_url = params['url']
						resp = session.get(m3u_url, headers=headers)
						resp.raise_for_status()
						data = resp.text

						if progress.wasCancelled():
							raise KeyboardInterrupt

				if resp.url.startswith('https://www.periscope.tv/w/'):
					# it was a periscope video page (html) instead
					doc = html.fromstring(data)
					meta = doc.cssselect("meta[property='og:image']")

					if not meta:
						raise Exception("could not find video info in referred page")

					image_url = urlparse(meta[0].attrib['content'])
					code      = RE_EXT.sub('', image_url.path.split("/")[2])
					m3u_url   = 'https://replay.periscope.tv/%s/playlist.m3u8' % code

					resp = session.get(m3u_url, headers=headers)
					resp.raise_for_status()
					data = resp.text

					if progress.wasCancelled():
						raise KeyboardInterrupt

				chunk_urls = [urljoin(m3u_url, line) for line in data.split('\n') if line and line[0] != '#']
				chunk_count = len(chunk_urls)

				progress.maximum = chunk_count
				start_time = time()

				with open(outfile,'wb') as fp:
					for i, chunk_url in enumerate(chunk_urls):
						print('downloading:',chunk_url)
						with closing(session.get(chunk_url, headers=headers, stream=True)) as resp:
							resp.raise_for_status()
							for data in resp.iter_content(8192):
								fp.write(data)
								if progress.wasCancelled():
									raise KeyboardInterrupt

						value   = i + 1
						elapsed = time() - start_time
						avgtime = elapsed / value
						esttime = avgtime * chunk_count
						remtime = esttime - elapsed

						progress.value = value
						progress.setLabelText('Downloading »%s« ETA -%s' % (outname, fmt_span(remtime)))
						if progress.wasCancelled():
							raise KeyboardInterrupt

		passive_popup('Filished saving video: '+outfile)

	except KeyboardInterrupt:
		print("\ndownload canceled by user")
		if outfile is not None:
			passive_popup('Download canceled by user: '+outfile)

if __name__ == '__main__':
	import sys
	try:
		get_video_from_m3u(*sys.argv[1:3])

	except Exception as e:
		traceback.print_exc()
		show_error(str(e))
