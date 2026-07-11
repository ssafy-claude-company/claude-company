from django.urls import path

from . import views

urlpatterns = [
    path("", views.projects),
    path("<slug:slug>/", views.project),
    path("<slug:slug>/scan/", views.project_scan),
    path("<slug:slug>/graph/", views.project_graph),
    path("<slug:slug>/concepts/", views.project_concepts),
]
