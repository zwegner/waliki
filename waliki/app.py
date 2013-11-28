import os

from functools import wraps

from flask import (Flask, render_template, flash, redirect, url_for, request)
from flask.ext.login import (LoginManager, login_required, current_user,
                             login_user, logout_user)
from flask.ext.script import Manager
from extensions.cache import cache
from signals import wiki_signals, page_saved, pre_display, pre_edit

import markup
import storage
import users
import wiki

def get_subclass_dict(cls):
    return dict([(c.__name__.lower(), c) for c in cls.__subclasses__()])

# Application Setup
# ~~~~~~~~~

app = Flask(__name__)
app.debug = True
app.config['CONTENT_DIR'] = os.path.abspath('content')
app.config['TITLE'] = 'wiki'
app.config['MARKUP'] = 'markdown'  # or 'restructucturedtext'
app.config['STORAGE'] = 'git'
app.config['THEME'] = 'elegant'  # more at waliki/static/codemirror/theme
try:
    app.config.from_pyfile(
        os.path.join(app.config.get('CONTENT_DIR'), 'config.py')
    )
except IOError:
    print ("Startup Failure: You need to place a "
           "config.py in your content directory.")

CACHE_DIR = os.path.join(app.config.get('CONTENT_DIR'), 'cache')
cache.init_app(app, config={'CACHE_TYPE': 'filesystem',
                            'CACHE_DIR': CACHE_DIR})
manager = Manager(app)

loginmanager = LoginManager()
loginmanager.init_app(app)
loginmanager.login_view = 'user_login'
markup_class = get_subclass_dict(markup.Markup)[app.config.get('MARKUP')]

# FIX ME: This monkeypatching is pollution crap .
#         Should be possible to import them wherever,
#         Wiki class should be a singleton.
app.wiki = wiki.Wiki(app.config.get('CONTENT_DIR'), markup_class)
app.signals = wiki_signals
app.EditorForm = wiki.EditorForm
app.user_manager = users.UserManager(app)

# Set up the storage engine. Must wait until the signals are constructed!
storage_class = get_subclass_dict(storage.StorageEngine)[app.config.get('STORAGE')]
storage_engine = storage_class(app)

# ugh, all the WTForms stuff needs access to app as a global...
wiki.app = app

@loginmanager.user_loader
def load_user(name):
    return app.user_manager.get_user(name)

def protect(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if app.config.get('PRIVATE') and not current_user.is_authenticated():
            return loginmanager.unauthorized()
        return f(*args, **kwargs)
    return wrapper

# Routes
# ~~~~~~

@app.route('/')
@protect
def home():
    page = app.wiki.get('home')
    if page:
        return display('home')
    return render_template('home.html')

@app.route('/index/')
@protect
def index():
    pages = app.wiki.index()
    return render_template('index.html', pages=pages)

@app.route('/<path:url>/')
@protect
def display(url):
    page = app.wiki.get(url)
    if not page:
        flash('The page "{0}" does not exist, '
              'feel free to make it now!'.format((url)), 'warning')
        return redirect(url_for('edit', url=wiki.urlify(url)))
    extra_context = {}
    pre_display.send(page, user=current_user, extra_context=extra_context)
    return render_template('page.html', page=page, **extra_context)

@app.route('/create/', methods=['GET', 'POST'])
@protect
def create():
    form = wiki.URLForm()
    if form.validate_on_submit():
        return redirect(url_for('edit', url=wiki.urlify(form.url.data)))
    return render_template('create.html', form=form)

@app.route('/<path:url>/_edit', methods=['GET', 'POST'])
@protect
def edit(url):
    page = app.wiki.get(url)
    form = wiki.EditorForm(obj=page)
    if form.validate_on_submit():
        if not page:
            page = app.wiki.get_bare(url)
        form.populate_obj(page)
        page.save()
        page.delete_cache()
        page_saved.send(page,
                        user=current_user,
                        message=form.message.data.encode('utf-8'))
        flash('"%s" was saved.' % page.title, 'success')
        return redirect(url_for('display', url=url))
    extra_context = {}
    pre_edit.send(page, url=url, user=current_user, extra_context=extra_context)
    return render_template('editor.html', form=form, page=page,
                           markup=markup_class, **extra_context)

@app.route('/preview/', methods=['POST'])
@protect
def preview():
    a = request.form
    data = {}
    data['html'], data['body'], data['meta'] = markup_class(a['body']).process()
    return data['html']

@app.route('/<path:url>/_move', methods=['GET', 'POST'])
@protect
def move(url):
    page = app.wiki.get_or_404(url)
    form = wiki.URLForm(obj=page)
    if form.validate_on_submit():
        newurl = form.url.data
        app.wiki.move(url, newurl)
        return redirect(url_for('.display', url=newurl))
    return render_template('move.html', form=form, page=page)

@app.route('/<path:url>/_delete', methods=['POST'])
@protect
def delete(url):
    page = app.wiki.get_or_404(url)
    app.wiki.delete(url)
    flash('Page "%s" was deleted.' % page.title)
    return redirect(url_for('home'))

@app.route('/tags/')
@protect
def tags():
    tags = app.wiki.get_tags()
    return render_template('tags.html', tags=tags)

@app.route('/tag/<string:name>/')
@protect
def tag(name):
    tagged = app.wiki.index_by_tag(name)
    return render_template('tag.html', pages=tagged, tag=name)

@app.route('/search/', methods=['GET', 'POST'])
@protect
def search():
    form = wiki.SearchForm()
    if form.validate_on_submit():
        results = app.wiki.search(form.term.data)
        return render_template('search.html', form=form,
                               results=results, search=form.term.data)
    return render_template('search.html', form=form, search=None)

@app.route('/user/login/', methods=['GET', 'POST'])
def user_login():
    form = wiki.LoginForm()
    if form.validate_on_submit():
        user = app.user_manager.get_user(form.name.data)
        login_user(user)
        user.set('authenticated', True)
        flash('Login successful.', 'success')
        return redirect(request.args.get("next") or url_for('index'))
    return render_template('login.html', form=form)

@app.route('/user/logout/')
@login_required
def user_logout():
    current_user.set('authenticated', False)
    logout_user()
    flash('Logout successful.', 'success')
    return redirect(url_for('index'))

@app.route('/user/')
def user_index():
    pass

@app.route('/user/signup/', methods=['GET', 'POST'])
def user_signup():
    form = SignupForm()
    if form.validate_on_submit():
        app.user_manager.add_user(form.name.data, form.password.data,
                       form.full_name.data, form.email.data)
        flash('You were registered successfully. Please login now.', 'success')
        return redirect(request.args.get("next") or url_for('index'))
    return render_template('signup.html', form=form)

@app.route('/user/<int:user_id>/')
def user_admin(user_id):
    pass

@app.route('/user/delete/<int:user_id>/')
def user_delete(user_id):
    pass

# Load extensions
for ext in app.config.get('EXTENSIONS', []):
    mod = __import__('extensions.%s' % ext, fromlist=['init'])
    mod.init(app)

if __name__ == '__main__':
    manager.run()
