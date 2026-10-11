"""Cloudinary storage backend for video files.

The default `MediaCloudinaryStorage` from `cloudinary_storage` uploads
everything with `resource_type='image'`. Cloudinary then rejects MP4s
with "Invalid image file" because they're not images.

This subclass calls `cloudinary.uploader.upload(..., resource_type='video')`
so MP4/WebM uploads go through Cloudinary's video pipeline.
"""

import cloudinary.uploader
from cloudinary_storage.storage import MediaCloudinaryStorage


class VideoCloudinaryStorage(MediaCloudinaryStorage):
    """Media storage that uploads to Cloudinary's video bucket."""

    def _upload(self, name, content):
        options = {
            'use_filename': True,
            'unique_filename': True,
            'resource_type': 'video',
        }
        return cloudinary.uploader.upload(content, **options)
