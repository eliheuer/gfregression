'''
Compare local fonts against fonts available on fonts.google.com
'''
from __future__ import print_function
from flask import Flask, request, render_template, redirect, url_for
from fontTools.ttLib import TTFont
from glob import glob
from uuid import uuid4
from collections import namedtuple
import ntpath
import requests
import re
import os
import shutil
import json
from urllib import urlopen
from zipfile import ZipFile
from StringIO import StringIO
from fontchecks import (
    modified_family_glyphs,
    new_family_glyphs,
    missing_family_glyphs,
    gsub_languages,
)

__version__ = 1.100


URL_PREFIX = 'https://fonts.google.com/download?family='

FONT_EXCEPTIONS = {
    'VT323': 'VT323',
    'AmaticSC': 'Amatic SC',
    'AmaticaSC': 'Amatica SC',
    'OldStandardTT': 'Old Standard TT',
}

LOCAL_FONTS_PATH = './static/localfonts/'
REMOTE_FONTS_PATH = './static/remotefonts/'

app = Flask(__name__, static_url_path='/static')

def dummy_text():
    with open('./dummy_text.txt', 'r') as txt_doc:
        return txt_doc.read()


def url_200_response(url):
    '''Check if the url mataches the Google fonts api url'''
    if requests.get(url).status_code == 200:
        return True
    return False


def _convert_camelcase(fam_name, seperator=' '):
    '''RubikMonoOne > Rubik+Mono+One'''
    if fam_name not in FONT_EXCEPTIONS:
        return re.sub('(?!^)([A-Z]|[0-9]+)', r'%s\1' % seperator, fam_name)
    else:
        fam_name = FONT_EXCEPTIONS[fam_name].replace(' ', seperator)
        return fam_name


def gf_download_url(local_fonts):
    """Assemble download url for families"""
    remote_families_name = set([_convert_camelcase(f.split('-')[0], '%20')
                               for f in local_fonts])
    remote_families_url_suffix = '|'.join(remote_families_name)
    return URL_PREFIX + remote_families_url_suffix


def download_family_from_gf(url):
    """Return a zipfile containing a font family hosted on fonts.google.com"""
    request = urlopen(url)
    return ZipFile(StringIO(request.read()))


def fonts_from_zip(zipfile, to):
    """download the fonts and store them locally"""
    unnest = False
    for file_name in zipfile.namelist():
        if file_name.endswith(".ttf"):
            zipfile.extract(file_name, to)
        if '/' in file_name:
            unnest = True
    if unnest:
        _unnest_folder(to)


def _unnest_folder(folder):
    """If multiple fonts have been downloaded, move them from sub dirs to
    parent dir"""
    for r, path, files, in os.walk(folder):
        for file in files:
            if file.endswith('.ttf'):
                shutil.move(os.path.join(r, file), folder)

    for f in os.listdir(folder):
        if os.path.isdir(os.path.join(folder, f)):
            os.rmdir(os.path.join(folder, f))


def fonts(paths, suffix):
    """Create a collection of css_property objects"""
    fonts = []
    for path in paths:
        name = ntpath.basename(path)[:-4]
        font = css_property(
            path=path.replace('\\', '/'),
            fullname=name,
            cssname='%s-%s' % (name, suffix),
            font=TTFont(path)
        )
        fonts.append(font)
    return fonts


def css_property(path, fullname, cssname, font):
    """Create the properties needed to load @fontface fonts"""
    Font = namedtuple('Font', ['path', 'fullname', 'cssname', 'font'])
    name = ntpath.basename(path)[:-4]
    font = Font(
        path=path,
        fullname=fullname,
        cssname=cssname,
        font=font
    )
    return font


def _delete_fonts(path):
    """Delete any ttfs in a specific folder"""
    for item in os.listdir(path):
        if os.path.isdir(os.path.join(path, item)):
            shutil.rmtree(os.path.join(path, item))
        else:
            if item.endswith('.ttf'):
                os.remove(os.path.join(path, item))


@app.route("/<uuid>")
def test_fonts(uuid):

    user_session_fonts = os.path.join(LOCAL_FONTS_PATH, uuid)
    local_fonts_paths = glob(user_session_fonts + '/*.ttf')
    local_fonts = fonts(local_fonts_paths, 'new')

    # Assemble download url for families
    remote_download_url = gf_download_url([i.fullname for i in local_fonts])
    # download last fonts from fonts.google.com
    if url_200_response(remote_download_url):
        remote_fonts_zip = download_family_from_gf(remote_download_url)
        fonts_from_zip(remote_fonts_zip, REMOTE_FONTS_PATH)

        remote_fonts_paths = glob(REMOTE_FONTS_PATH + '*.ttf')
        remote_fonts = fonts(remote_fonts_paths, 'old')
    else:
        return 'Font is not hosted on fonts.google.com'

    shared_fonts = set([i.fullname for i in local_fonts]) & \
                   set([i.fullname for i in remote_fonts])

    local_fonts = {f.fullname: f for f in local_fonts
                   if f.fullname in shared_fonts}
    remote_fonts = {f.fullname: f for f in remote_fonts
                    if f.fullname in shared_fonts}

    # Add font checks
    changed_glyphs = modified_family_glyphs(local_fonts, remote_fonts)
    new_glyphs = new_family_glyphs(local_fonts, remote_fonts)
    missing_glyphs = missing_family_glyphs(local_fonts, remote_fonts)

    languages = gsub_languages(local_fonts)

    # css hook to swap remote fonts to local fonts and vice versa
    to_local_fonts = ','.join([local_fonts[i].cssname for i in local_fonts])
    to_remote_fonts = ','.join([remote_fonts[i].cssname for i in remote_fonts])
    return render_template(
        'index.html',
        dummy_text=dummy_text(),
        local_fonts=local_fonts.values(),
        remote_fonts=remote_fonts.values(),
        changed_glyphs=changed_glyphs,
        new_glyphs=new_glyphs,
        missing_glyphs=missing_glyphs,
        languages=languages,
        to_local_fonts=to_local_fonts,
        to_remote_fonts=to_remote_fonts
    )


@app.route('/')
def index():
    """Drag n drop font families to be tested.

    Each user who runs this view will clear the font cache. This will not
    affect other users, as long as they don't refresh their browsers"""
    _delete_fonts(LOCAL_FONTS_PATH)
    _delete_fonts(REMOTE_FONTS_PATH)
    return render_template('upload.html')


@app.route('/upload-fonts', methods=["POST"])
def upload_fonts():
    """Handle the upload of a file."""
    form = request.form

    # Create a unique "session ID" for this particular batch of uploads.
    upload_key = str(uuid4())

    # Is the upload using Ajax, or a direct POST by the form?
    is_ajax = False
    if form.get("__ajax", None) == "true":
        is_ajax = True

    # Target folder for these uploads.
    target = LOCAL_FONTS_PATH + upload_key
    os.mkdir(target)

    for upload in request.files.getlist("file"):
        filename = upload.filename.rsplit("/")[0]
        destination = "/".join([target, filename])
        upload.save(destination)

    if is_ajax:
        return ajax_response(True, upload_key)
    else:
        return redirect(url_for("test_fonts", uuid=upload_key))


def ajax_response(status, msg):
    status_code = "ok" if status else "error"
    return json.dumps(dict(
        status=status_code,
        msg=msg,
    ))


if __name__ == "__main__":
    app.config['STATIC_FOLDER'] = 'static'
    app.run(debug=True)
