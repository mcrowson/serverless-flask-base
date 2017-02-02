from flask import current_app, session
from flask.ext.login import AnonymousUserMixin, UserMixin
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from itsdangerous import BadSignature, SignatureExpired
import boto3

from .. import login_manager


class UserHandler(object):

    @staticmethod
    def list_users(boto3_session=None):
        if not boto3_session:
            boto3_session = boto3.Session()

        client = boto3_session.client('cognito-idp')
        response = client.list_users(
            UserPoolId=current_app.config['COGNITO_POOL_ID']
        )
        return response.get('Users', [])

    @staticmethod
    def get_user(email, boto3_session=None):
        if not boto3_session:
            boto3_session = boto3.Session()

        client = boto3_session.client('cognito-idp')
        try:
            response = client.admin_get_user(
                UserPoolId=current_app.config['COGNITO_POOL_ID'],
                Username=email
            )
        except Exception:
            # Ideally the exception UserNotFoundException would be specified, but it is declared in a json within botocore.
            return

        user = User()
        user.enabled = response.get('Enabled', False)
        for attribute in response.get('UserAttributes'):
            setattr(user, attribute[u'Name'], attribute[u'Value'])

        return user

    def authenticate_user(self, username, password, boto3_session=None):
        if not boto3_session:
            boto3_session = boto3.Session()

        client = boto3_session.client('cognito-idp')
        try:
            response = client.admin_initiate_auth(
                UserPoolId=current_app.config['COGNITO_POOL_ID'],
                ClientId=current_app.config['COGNITO_APP_CLIENT_ID'],
                AuthFlow='ADMIN_NO_SRP_AUTH',
                AuthParameters={'USERNAME': username,
                                'PASSWORD': password})
        except Exception as m:
            print m.message
            # Ideally the exception UserNotFoundException would be specified, but it is declared in a json within botocore.
            return

        session['expires_in'] = response['AuthenticationResult']['ExpiresIn']
        session['id_token'] = response['AuthenticationResult']['IdToken']
        session['refresh_token'] = response['AuthenticationResult']['RefreshToken']
        session['access_token'] = response['AuthenticationResult']['AccessToken']
        session['token_type'] = response['AuthenticationResult']['TokenType']

        return self.get_user(email=username)


class User(UserMixin, object):

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)

        self.client = boto3.client('cognito-idp')
        self.email = None
        self.given_name = None,
        self.family_name = None,
        self.session = None
        self.role = None

    def get_id(self):
        """Overriding because Cognito has username instead of an id field. We are using email/username interchangably"""
        return self.email

    def full_name(self):
        return '%s %s' % (self.given_name, self.family_name)

    def is_admin(self):
        return self.member_of_group('administrator')

    def generate_confirmation_token(self, expiration=604800):
        """Generate a confirmation token to email a new user."""
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'confirm': self.email})

    def generate_email_change_token(self, new_email, expiration=3600):
        """Generate an email change token to email an existing user."""
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'change_email': self.email, 'new_email': new_email})

    def generate_password_reset_token(self, expiration=3600):
        """Generate a password reset change token to email to an existing user."""
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'reset': self.email})

    def confirm_account(self, token):
        """Verify that the provided token is for this user's id."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except (BadSignature, SignatureExpired):
            return False
        if data.get('confirm') != self.email:
            return False

        self.client.admin_configm_signup(
            UserPoolId=current_app.config['COGNITO_POOL_ID'],
            Username=self.email
        )
        return True

    def reset_password(self, token, previous_password, new_password, access_token):
        """Verify the new password for this user."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except (BadSignature, SignatureExpired):
            return False
        if data.get('reset') != self.email:
            return False

        self.client.change_password(
            PreviousPassword=previous_password,
            ProposedPassword=new_password,
            AccessToken=access_token
        )
        return True

    def add_to_group(self, group_name):
        """Adds user to a Cognito Group"""
        self.client.admin_add_user_to_group(
            UserPoolId=current_app.config['COGNITO_POOL_ID'],
            Username=self.email,
            GroupName=group_name
        )

    def member_of_group(self, group_name):
        """Returns True if user is a member of the named group"""
        response = self.client.admin_list_groups_for_user(
            UserPoolId=current_app.config['COGNITO_POOL_ID'],
            Username=self.email,
        )

        for group in response.get('Groups', []):
            if group['GroupName'] == group_name:
                return True
        return False


    @staticmethod
    def generate_fake(count=100, **kwargs):
        """Generate a number of fake users for testing."""

        # TODO have placebo mock up responses with these 100 users rather than generate them each time
        from random import seed, choice
        from faker import Faker

        fake = Faker()

        seed()
        for i in range(count):
            u = User(
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=fake.email(),
                password=fake.password(),
                confirmed=True,
                role=choice(roles),
                **kwargs)

            u.save()

    def __repr__(self):
        return '<User \'%s\'>' % self.full_name()


class AnonymousUser(AnonymousUserMixin):
    @staticmethod
    def member_of_group(_):
        return False

    @staticmethod
    def is_admin():
        return False


login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def user_loader(email, boto3_session=None):
    handler = UserHandler()
    return handler.get_user(email, boto3_session=boto3_session)


@login_manager.request_loader
def request_loader(request, boto3_session=None):
    email = request.form.get('email')
    password = request.form.get('password')

    handler = UserHandler()

    return handler.authenticate_user(username=email, password=password)
