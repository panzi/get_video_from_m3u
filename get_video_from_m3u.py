#!/usr/bin/python

from __future__ import print_function

import os
import subprocess
import shlex
from urlparse import urljoin

CAPTION = 'Get Video from M3U'

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
	elif status == 2:
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

def get_save_filename(dirname=None):
	if dirname is None:
		dirname = os.getenv("HOME") or os.path.abspath(".")
	while True:
		outfile = text_cmd('kdialog','--getsavefilename',dirname,'--caption',CAPTION)
		if not os.path.exists(outfile) or \
			warning_yes_no('File already exists. Do you want to overwrite it?'):
			return outfile

def passive_popup(text,timeout=5):
	subprocess.check_call(['kdialog','--passivepopup',text,str(timeout),'--caption',CAPTION])

def show_error(text):
	subprocess.check_call(['kdialog','--error',text,'--caption',CAPTION])

class Progressbar(object):
	def __init__(self,text,maximum):
		self._value  = 0
		self._handle = text_cmd('kdialog','--progressbar',text,str(maximum),'--caption',CAPTION).split()

	def step(self):
		self.value = self._value + 1
	
	def _get_value(self):
		value = int(check_call_errmsg(['qdbus',self._handle[0], self._handle[1], 'org.kde.kdialog.ProgressDialog.value'],
			stdout=subprocess.PIPE).strip())
		self._value = value
		return value

	def _set_value(self,value):
		check_call_errmsg(['qdbus',self._handle[0], self._handle[1], 'org.kde.kdialog.ProgressDialog.value', str(value)])
		self._value = value

	value = property(_get_value, _set_value)
	
	def close(self):
		check_call_errmsg(['qdbus',self._handle[0], self._handle[1], 'org.kde.kdialog.ProgressDialog.close'])
	
	def __enter__(self):
		return self
	
	def __exit__(self, exc_type=None, exc_value=None, trackeback=None):
		self.close()

def get_video_from_m3u(curl=None):
	if curl is None:
		curl = inputbox('Paste M3U URL/cURL from network tab:')
	if not curl.startswith('curl '):
		url = curl
		curl = ['curl',curl]
		url_index = 1
	else:
		curl = shlex.split(curl)
		i = 1
		url_index = None
		while i < len(curl):
			arg = curl[i]
			if arg == '-H':
				val = curl[i+1]
				lval = val.lower()
				if lval.startswith('if-none-match:') or lval.startswith('if-modified-since:'):
					del curl[i:i+2]
				else:
					i += 2
			elif arg == '--compressed':
				i += 1
			elif arg.startswith('-'):
				raise ValueError('Cannot parse cURL command line because of unknown argument: '+arg)
			else:
				if url_index is not None:
					raise ValueError('Cannot parse cURL command line because it contains more than one url:\n%s\n%s'%(curl[url_index], arg))
				url_index = i
				i += 1

	outfile = get_save_filename()

	curl.append('--silent')
	curl.append('--show-error')

	m3uurl = curl[url_index]
	m3u = check_call_errmsg(curl,stdout=subprocess.PIPE)
	chunks = [urljoin(m3uurl, line) for line in m3u.split('\n') if line and line[0] != '#']

	with Progressbar('Downloading %s:' % os.path.split(outfile)[1],len(chunks)) as progress:
		with open(outfile,'wb') as fp:
			for chunk in chunks:
				curl[url_index] = chunk
				check_call_errmsg(curl,stdout=fp)
				progress.step()

	passive_popup('Filished saving video: '+outfile)

if __name__ == '__main__':
	import sys
	try:
		if len(sys.argv) > 1:
			get_video_from_m3u(sys.argv[1])
		else:
			get_video_from_m3u()

	except KeyboardInterrupt:
		print("\ncanceled by user interrupt")
	except Exception as e:
		show_error(str(e))
