"""feedback URL — 호스트 서비스가 include("feedback.urls")로 얹는다(이식 지점 2/2)."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.items, name="feedback-items"),
    path("summary/", views.summary, name="feedback-summary"),
    path("<int:item_id>/", views.item, name="feedback-item"),
    path("<int:item_id>/comments/", views.comments, name="feedback-comments"),
    path("<int:item_id>/comments/<int:comment_id>/", views.comment, name="feedback-comment"),
]
