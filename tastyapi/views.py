from django.core.urlresolvers import reverse
import json
import base64
import datetime
from itsdangerous import URLSafeTimedSerializer

from getenv import env
from django.shortcuts import render
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.http import HttpResponseForbidden, HttpResponseBadRequest
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password

from .models import User, generate_confirmation_code
from metpetdb import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import get_template
from django.template import Context


@csrf_exempt
@transaction.commit_on_success
def register(request):
    json_data = json.loads(request.body)
    print(json_data)

    try:
        User.objects.get(email=json_data['email'])
        data = {
            'result': 'failed',
            'message': 'email is already taken'
        }
        return HttpResponseForbidden(json.dumps(data),
                                     content_type='application/json')
    except:
        allowed_params = ["name", "email", "address", "city", "password",
                          "province", "country", "postal_code"]

        user = User()
        user.version = 1
        for param, value in json_data.iteritems():
            if param in allowed_params:
                setattr(user, param, value)
    user.password = bytearray(base64.b64encode(make_password(
                                                       json_data['password'])))
    user.enabled = 'N'
    user.contributor_enabled = 'N'
    user.confirmation_code = ''
    user.contributor_code = ''
    user.django_user = None
    user.save()
    data = {
        'result': 'success',
        'message': 'user registration successful',
        'email': user.email
    }
    return HttpResponse(json.dumps(data), content_type='application/json')


@csrf_exempt
def authenticate(request):
    try:
        user = User.objects.get(email=request.POST.get('email'))
    except:
        data = {
            'result': 'failed',
            'message': 'invalid email address'
        }
        return HttpResponseForbidden(json.dumps(data),
                                     content_type='application/json')
    if user.django_user.check_password(request.POST.get('password')):
        data = {
            'result': 'success',
            'email': user.email,
            'api_key': user.django_user.api_key.key
        }
        return HttpResponse(json.dumps(data), content_type='application/json')
    else:
        data = {
            'status': 'failed',
            'message': 'authentication failed'
        }
        return HttpResponseForbidden(json.dumps(data),
                                     content_type='application/json')


@csrf_exempt
def reset_password(request, token=None):
    serializer = URLSafeTimedSerializer(env('RESET_PWD_KEY'),
                                        salt=env('RESET_PWD_SALT'))
    if request.method == 'POST':
        try:
            if request.POST.get('token'):
                sig_okay, email = serializer.loads_unsafe(request.POST.get('token'))
                if not sig_okay:
                    data = {'result': 'failed',
                            'reason': 'reset token has expired'}
                    return HttpResponseBadRequest(json.dumps(data),
                                                  content_type='application/json')
            else:
                email = request.POST.get('email', None)
            user = User.objects.get(email=email)
        except:
            return HttpResponseNotFound("Invalid email address")

        if user.enabled == 'Y':
            if request.POST.get('password'):
                try:
                    user.password = bytearray(base64.b64encode(make_password(
                                                request.POST.get('password'))))
                    user.save()
                    django_user = user.django_user
                    django_user.password = base64.b64decode(user.password)
                    django_user.save()
                    data = {'result': 'success',
                            'email': user.email,
                            'api_key': user.django_user.api_key.key}
                    return HttpResponse(json.dumps(data),
                                        content_type='application/json')
                except:
                    data = {'result': 'failed'}
                    return HttpResponseBadRequest(json.dumps(data),
                                              content_type='application/json')
            else:
                reset_token = serializer.dumps(user.email)

                data = {
                    'status': 'success',
                    'email': user.email,
                    'reset_token': reset_token
                }
                return HttpResponse(json.dumps(data),
                                    content_type='application/json')

    if request.method == 'GET':
        if token:
            serializer = URLSafeTimedSerializer(env('RESET_PWD_KEY'),
                                                salt=env('RESET_PWD_SALT'))
            sig_okay, email = serializer.loads_unsafe(token, max_age=86400)
            if sig_okay:
                data = {
                    'result': 'success',
                    'email': email
                }
                return HttpResponse(json.dumps(data),
                                    content_type='application/json')

        data = {'status': 'failed',
                'reason': 'invalid token'}
        return HttpResponseBadRequest(json.dumps(data),
                                      content_type='application/json')


@transaction.commit_on_success
def confirm(request, conf_code):
    try:
        user = User.objects.get(confirmation_code=conf_code)
    except User.DoesNotExist:
        return HttpResponseNotFound("The confirmation code is invalid.")

    if user.enabled == 'Y':
        return HttpResponseForbidden("This account has already been confirmed")

    if user.auto_verify(conf_code):
        return HttpResponse("Thank you for confirming your email address!")
    else:
        return HttpResponse("Unable to confirm your email address.")


@transaction.commit_on_success
def request_contributor_access(request):
    try:
        user = User.objects.get(email=request.GET['email'])
    except:
        return HttpResponseNotFound("Invalid email address")

    if user.enabled == "N":
        return HttpResponseForbidden("Please confirm your email address first.")

    if user.contributor_code != '':
        return HttpResponseForbidden("You have already requested access.")

    if user.contributor_enabled == 'Y':
        return HttpResponseForbidden("This user is already a contributor")

    allowed_params = ['professional_url', 'research_interests',
                      'institution', 'reference_email']

    for param, value in request.GET.iteritems():
        if param in allowed_params:
            setattr(user, param, value)

    user.contributor_code = generate_confirmation_code(user.email)
    user.save()

    plaintext = get_template('request_contributor_access.txt')
    html      = get_template('request_contributor_access.html')

    d = Context({ 'name': env('SUPERUSER_NAME'),
                  'user': user,
                  'host_name': settings.HOST_NAME })

    subject = 'hello'
    from_email = env('DEFAULT_FROM_EMAIL')
    to = env('SUPERUSER_EMAIL')

    text_content = plaintext.render(d)
    html_content = html.render(d)
    msg = EmailMultiAlternatives(subject, text_content, from_email, [to])
    msg.attach_alternative(html_content, "text/html")
    msg.send()
    return HttpResponse("Your request to be added as a contributor has been submitted")


@transaction.commit_on_success
def grant_contributor_access(request, contributor_code):
    try:
        user = User.objects.get(contributor_code=contributor_code)
    except User.DoesNotExist:
        return HttpResponseNotFound("The contributor code is invalid.")

    if user.contributor_enabled == 'Y':
        return HttpResponseForbidden("This user is already a contributor.")

    if user.manual_verify():
        return HttpResponse("This user is now a contributor!")
    else:
        return HttpResponseForbidden("Unable to make this user a contributor")
