from django.urls import path

from . import views

urlpatterns = [
    path("", views.projects),
    path("<slug:slug>/", views.project),
    path("<slug:slug>/scan/", views.project_scan),
    path("<slug:slug>/graph/", views.project_graph),
    path("<slug:slug>/nodes/", views.nodes),
    path("<slug:slug>/nodes/<path:nid>/", views.node),
    path("<slug:slug>/edges/", views.edges),
    path("<slug:slug>/edges/<int:eid>/", views.edge),
    path("<slug:slug>/views/", views.views_),
    path("<slug:slug>/views/<slug:vid>/", views.view_),
]
