from django.urls import path

from . import canvas_api, views

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
    path("<slug:slug>/canvases/", canvas_api.canvases),
    path("<slug:slug>/canvases/<slug:cid>/", canvas_api.canvas),
    path("<slug:slug>/canvases/<slug:cid>/objects/", canvas_api.objects),
    path("<slug:slug>/canvases/<slug:cid>/items/", canvas_api.items),
    path("<slug:slug>/canvases/<slug:cid>/items/<int:iid>/", canvas_api.item),
    path("<slug:slug>/canvases/<slug:cid>/links/", canvas_api.links),
    path("<slug:slug>/canvases/<slug:cid>/links/<int:lid>/", canvas_api.link),
]
