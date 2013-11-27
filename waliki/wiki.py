import os
import re

from extensions.cache import cache
from flask import abort
from flask.ext.wtf import Form
from wtforms import (TextField, TextAreaField, PasswordField)
from wtforms.validators import (Required, ValidationError, Email)

import markup

# Wiki classes
# ~~~~~~~~~~~~

class Page(object):
    def __init__(self, path, url, new=False, markup=markup.Markdown):
        self.path = path
        self.url = url
        self.markup = markup
        self._meta = {}
        if not new:
            self.load()
            self.render()

    def load(self, content=None):
        if not content:
            with open(self.path, 'rU') as f:
                content = f.read().decode('utf-8')
        self.content = self.markup(content)

    def render(self):
        self._html, self.body, self._meta = self.content.process()

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.path)

    def save(self, update=True):
        folder = os.path.dirname(self.path)
        if not os.path.exists(folder):
            os.makedirs(folder)
        with open(self.path, 'w') as f:
            for key in sorted(self._meta.keys()):
                value = self._meta[key]
                line = self.markup.META_LINE % (key, value)
                f.write(line.encode('utf-8'))
            f.write('\n'.encode('utf-8'))
            f.write(self.body.replace('\r\n', os.linesep).encode('utf-8'))
        if update:
            self.load()
            self.render()

    @property
    def meta(self):
        return self._meta

    def __getitem__(self, name):
        item = self._meta[name]
        if len(item) == 1:
            return item[0]
        return item

    def __setitem__(self, name, value):
        self._meta[name] = value

    @property
    def html(self):
        return self._html

    @cache.memoize()
    def __html__(self):
        return self.html

    def delete_cache(self):
        cache.delete(self.__html__.make_cache_key(self.__html__.uncached,
                                                  self))

    @property
    def title(self):
        return self['title']

    @title.setter               # NOQA
    def title(self, value):
        self['title'] = value

    @property
    def tags(self):
        return self['tags']

    @tags.setter               # NOQA
    def tags(self, value):
        self['tags'] = value

class Wiki(object):
    def __init__(self, root, markup=markup.Markdown):
        self.root = root
        self.markup = markup

    def path(self, url):
        return os.path.join(self.root, url + self.markup.EXTENSION)

    def exists(self, url):
        path = self.path(url)
        return os.path.exists(path)

    def get(self, url):
        path = os.path.join(self.root, url + self.markup.EXTENSION)
        if self.exists(url):
            return Page(path, url, markup=self.markup)
        return None

    def get_or_404(self, url):
        page = self.get(url)
        if page:
            return page
        abort(404)

    def get_bare(self, url):
        path = self.path(url)
        if self.exists(url):
            return False
        return Page(path, url, new=True, markup=self.markup)

    def move(self, url, newurl):
        os.rename(
            os.path.join(self.root, url) + self.markup.EXTENSION,
            os.path.join(self.root, newurl) + self.markup.EXTENSION
        )

    def delete(self, url):
        path = self.path(url)
        if not self.exists(url):
            return False
        os.remove(path)
        return True

    def index(self, attr=None):
        def _walk(directory, path_prefix=()):
            for name in os.listdir(directory):
                fullname = os.path.join(directory, name)
                if os.path.isdir(fullname):
                    _walk(fullname, path_prefix + (name,))
                elif name.endswith(self.markup.EXTENSION):
                    ext_len = len(self.markup.EXTENSION)
                    if not path_prefix:
                        url = name[:-ext_len]
                    else:
                        url = os.path.join(path_prefix[0], name[:-ext_len])
                    if attr:
                        pages[getattr(page, attr)] = page
                    else:
                        pages.append(Page(fullname, url.replace('\\', '/'),
                                          markup=self.markup))
        if attr:
            pages = {}
        else:
            pages = []
        _walk(self.root)
        if not attr:
            return sorted(pages, key=lambda x: x.title.lower())
        return pages

    def get_by_title(self, title):
        pages = self.index(attr='title')
        return pages.get(title)

    def get_tags(self):
        pages = self.index()
        tags = {}
        for page in pages:
            pagetags = set(page.tags.split(','))
            for tag in pagetags:
                tag = tag.strip()
                if tag == '':
                    continue
                elif tags.get(tag):
                    tags[tag].append(page)
                else:
                    tags[tag] = [page]
        return tags

    def index_by_tag(self, tag):
        pages = self.index()
        tagged = []
        for page in pages:
            if tag in page.tags:
                tagged.append(page)
        return sorted(tagged, key=lambda x: x.title.lower())

    def search(self, term, attrs=['title', 'tags', 'body']):
        pages = self.index()
        regex = re.compile(term)
        matched = []
        for page in pages:
            for attr in attrs:
                if regex.search(getattr(page, attr)):
                    matched.append(page)
                    break
        return matched

# Forms
# ~~~~~

class URLForm(Form):
    url = TextField('', [Required()])

    def validate_url(form, field):
        if wiki.exists(field.data):
            raise ValidationError('The URL "%s" exists already.' % field.data)

    def clean_url(self, url):
        return markup.urlify(url)

class SearchForm(Form):
    term = TextField('', [Required()])

class EditorForm(Form):
    title = TextField('', [Required()])
    body = TextAreaField('', [Required()])
    tags = TextField('')
    message = TextField('')

class LoginForm(Form):
    name = TextField('Username', [Required()])
    password = PasswordField('Password', [Required()])

    def validate_name(form, field):
        user = app.user_manager.get_user(field.data)
        if not user:
            raise ValidationError('This username does not exist.')

    def validate_password(form, field):
        user = app.user_manager.get_user(form.name.data)
        if not user:
            return
        if not user.check_password(field.data):
            raise ValidationError('Username and password do not match.')

class SignupForm(Form):
    name = TextField('Username', [Required()])
    email = TextField('Email', [Required(), Email()])
    full_name = TextField('Full name')
    password = PasswordField('Password', [Required()])

    def validate_name(form, field):
        user = app.user_manager.get_user(field.data)
        if user:
            raise ValidationError('This username is already taken')

    def validate_password(form, field):
        if len(field.data) < 4:
            raise ValidationError('The password is too short')
