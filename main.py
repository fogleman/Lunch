import datetime
import functools
import os
from flask import Flask, render_template, url_for, g, session, request, redirect, flash
from flaskext.wtf import Form, TextField, HiddenField, SelectField, DateField, validators
from flaskext.sqlalchemy import SQLAlchemy
from flaskext.openid import OpenID

# Flask
app = Flask(__name__)

app.config['SECRET_KEY'] = 'SECRET_KEY'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lunch.db'
app.config['OPENID_FS_STORE_PATH '] = os.path.join(app.root_path, 'openid')

db = SQLAlchemy(app)
oid = OpenID(app)

# Helpers
def static(path):
    root = app.config.get('STATIC_ROOT')
    if root is None:
        return url_for('static', filename=path)
    return os.path.join(root, path)

@app.context_processor
def context_processor():
    return dict(static=static)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    openid = db.Column(db.String(256), nullable=False, unique=True)
    name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(256), nullable=False)
    def __init__(self, openid, name, email):
        self.openid = openid
        self.name = name
        self.email = email
    def __repr__(self):
        return '<User %r>' % self.name

class Restaurant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    # photo(s)
    # category
    def __init__(self, name):
        self.name = name
    def __repr__(self):
        return '<Restaurant %r>' % self.name
    def count_lunches(self, user):
        if user is None:
            return 0
        return self.lunches.filter_by(user=user).count()

class Lunch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('lunches', lazy='dynamic'))
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurant.id'), nullable=False)
    restaurant = db.relationship('Restaurant', backref=db.backref('lunches', lazy='dynamic'))
    rating = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'date'),)
    def __init__(self, date, user, restaurant, rating=0, notes=''):
        self.date = date
        self.user = user
        self.restaurant = restaurant
        self.rating = rating
        self.notes = notes
    def __repr__(self):
        return '<Lunch %r>' % self.id

# OpenID
@app.before_request
def before_request():
    try:
        g.user = User.query.filter_by(openid=session['openid']).first()
    except Exception:
        g.user = None

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/logout')
def logout():
    session.pop('openid', None)
    return redirect(oid.get_next_url())

@app.route('/login', methods=['GET', 'POST'])
@oid.loginhandler
def login():
    if g.user:
        return redirect(oid.get_next_url())
    if request.method == 'POST':
        openid = request.form['openid']
        return oid.try_login(openid, ask_for=['email', 'fullname', 'nickname'])
    return render_template('login.html', next=oid.get_next_url(), error=oid.fetch_error())

@oid.after_login
def after_login(response):
    openid = response.identity_url
    user = User.query.filter_by(openid=openid).first()
    if user:
        session['openid'] = user.openid
        return redirect(oid.get_next_url())
    return redirect(url_for('profile',
        next=oid.get_next_url(),
        openid=openid,
        name=response.nickname or response.fullname,
        email=response.email))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    class ProfileForm(Form):
        next = HiddenField()
        openid = HiddenField('OpenID', [validators.Required()])
        name = TextField('Display Name', [validators.Length(3, 64)])
        email = TextField('Email', [validators.Length(6, 256)])
    form = ProfileForm(request.values, g.user, next=oid.get_next_url())
    if form.validate_on_submit():
        if g.user:
            g.user.name = form.name.data
            g.user.email = form.email.data
            db.session.commit()
        else:
            user = User(form.openid.data, form.name.data, form.email.data)
            db.session.add(user)
            db.session.commit()
            session['openid'] = user.openid
        return redirect(oid.get_next_url())
    return render_template('profile.html', form=form)

# Views
@app.route('/')
def index():
    if g.user:
        lunches = Lunch.query.filter_by(user=g.user).order_by(db.desc('date'))
    else:
        lunches = []
    return render_template('index.html', lunches=lunches)

@app.route('/today', methods=['GET', 'POST'])
@login_required
def today():
    class TodayForm(Form):
        next = HiddenField()
        date = DateField('Date', default=datetime.date.today)
        restaurant = SelectField('Restaurant', [validators.Required()], coerce=int)
        notes = TextField('Notes')
        def validate(self):
            if not Form.validate(self):
                return False
            lunch = Lunch.query.filter_by(date=self.date.data, user=g.user).first()
            if lunch:
                self.date.errors.append("You have already recorded this date's lunch!")
                return False
            return True
    restaurants = Restaurant.query.order_by('name')
    form = TodayForm(request.values)
    form.restaurant.choices = [(x.id, x.name) for x in restaurants]
    form.restaurant.choices.insert(0, (0, ''))
    if form.validate_on_submit():
        date = form.date.data
        restaurant = Restaurant.query.get_or_404(form.restaurant.data)
        notes = form.notes.data
        lunch = Lunch(date, g.user, restaurant, 0, notes)
        db.session.add(lunch)
        db.session.commit()
        return redirect(url_for('today'))
    return render_template('today.html', form=form)

@app.route('/restaurants')
def restaurants():
    restaurants = Restaurant.query.order_by('name')
    return render_template('restaurants.html', restaurants=restaurants)

@app.route('/add_restaurant', methods=['GET', 'POST'])
@login_required
def add_restaurant():
    class RestaurantForm(Form):
        restaurant_name = TextField('Name', [validators.Length(3, 64)])
        def validate(self):
            if not Form.validate(self):
                return False
            name = form.restaurant_name.data
            restaurant = Restaurant.query.filter_by(name=name).first()
            if restaurant:
                self.restaurant_name.errors.append('Restaurant names must be unique')
                return False
            return True
    form = RestaurantForm(request.values)
    if form.validate_on_submit():
        name = form.restaurant_name.data
        restaurant = Restaurant(name)
        db.session.add(restaurant)
        db.session.commit()
        flash('Successfully added restaurant: "%s"' % name)
        return redirect(url_for('add_restaurant'))
    return render_template('add_restaurant.html', form=form)

# Main
if __name__ == '__main__':
    #db.drop_all()
    #db.create_all()
    app.run(host='0.0.0.0', debug=True)
