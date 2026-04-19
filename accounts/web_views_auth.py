from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

def _dashboard_name_for_role(role):
    return {
        'admin': 'admin-dashboard',
        'manager': 'manager-dashboard',
        'employee': 'employee-dashboard',
    }.get(role, 'login')


def login_view(request):
    if request.method == 'GET':
        if request.user.is_authenticated:
            dashboard_name = _dashboard_name_for_role(getattr(request.user, 'role', None))
            if dashboard_name != 'login':
                return redirect(dashboard_name)
        error = request.session.pop('login_error', None)
        remember_checked = bool(request.session.pop('login_remember', False))
        context = {'remember_checked': remember_checked}
        if error:
            context['error'] = error
        return render(request, 'auth/login.html', context)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember') == 'on'
        user = authenticate(request, username=username, password=password)
        if user is None:
            User = get_user_model()
            inactive_user = User.objects.filter(username__iexact=username, is_active=False).first()
            if inactive_user:
                request.session['login_error'] = 'Ваш аккаунт деактивирован.'
                request.session['login_remember'] = remember_me
                return redirect('login')
            request.session['login_error'] = 'Неверный логин или пароль.'
            request.session['login_remember'] = remember_me
            return redirect('login')
        if not user.is_active:
            request.session['login_error'] = 'Ваш аккаунт деактивирован.'
            request.session['login_remember'] = remember_me
            return redirect('login')
        login(request, user)
        if remember_me:
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        else:
            request.session.set_expiry(0)
        return redirect(_dashboard_name_for_role(getattr(user, 'role', None)))
    return render(request, 'auth/login.html')


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

