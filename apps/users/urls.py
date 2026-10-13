from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf/", views.CsrfView.as_view()),
    path("auth/code/request/", views.CodeRequestView.as_view()),
    path("auth/code/verify/", views.CodeVerifyView.as_view()),
    path("auth/logout/", views.LogoutView.as_view()),
    path("me/", views.MeView.as_view()),
    path("me/bonus/", views.BonusView.as_view()),
]
