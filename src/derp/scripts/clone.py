#!/usr/bin/env python3

import cv2
import numpy as np
import os
import PIL
import torch
from torch.autograd import Variable
from derp.component import Component
import derp.util
import derp.imagemanip

class Clone(Component):

    def __init__(self, config, full_config):
        super(Clone, self).__init__(config, full_config)
        
        self.config = config
        self.no_cuda = 'no_cuda' in full_config and full_config['no_cuda']
        
        # Which config is our settings coming from
        self.source_config = derp.util.find_component_config(full_config, config['camera_name'])

        # Prepare camera inputs
        self.bbox = derp.imagemanip.get_patch_bbox(self.config['thumb'],
                                                   self.source_config)
        self.size = (config['thumb']['width'], config['thumb']['height'])

        # Prepare model
        self.model = None
        if 'model_dir' in full_config and full_config['model_dir'] is not None:
            model_path = derp.util.find_matching_file(full_config['model_dir'], 'clone.pt$')
            if model_path is not None:
                self.model = torch.load(model_path)
                self.model.eval()

        # Useful variables for params
        self.prev_steer = 0
        self.prev_speed = 0

        # Data saving
        self.out_buffer = []
        self.frame_counter = 0  


    # Prepare input image
    def prepare_thumb(self, state):
        frame = state[self.config['camera_name']]
        patch = frame[self.bbox.y : self.bbox.y + self.bbox.h,
                      self.bbox.x : self.bbox.x + self.bbox.w]
        thumb = cv2.resize(patch, self.size, interpolation=cv2.INTER_AREA)
        return thumb


    # Prepare status
    def prepare_status(self, state):
        if len(self.config['status']) == 0:
            return None
        status = np.zeros(len(self.config['status']), dtype=np.float32)
        for i, sd in enumerate(self.config['status']):
            status[i] = state[sd['field']] * sd['scale']
        return status
    

    # Prepare input batch
    def prepare_batch(self, thumbs, status):

        # Prepare thumbs
        if len(thumbs.shape) == 3:
            thumbs = np.reshape(thumbs, [1] + list(thumbs.shape))
        thumbs = thumbs.transpose((0, 3, 1, 2))
        thumbs = torch.from_numpy(thumbs).float()
        if not self.no_cuda:
            thumbs = thumbs.cuda()
        thumbs /= 255 # normalize
        thumbs = Variable(thumbs)

        # Prepare status variable
        if status is not None:
            if len(status.shape) == 1:
                status = np.reshape(status, [1] + list(status.shape))
            status = torch.from_numpy(status).float()
            if not self.no_cuda:
                status = status.cuda()
            status = Variable(status)
            
        return thumbs, status


    def predict(self, state):
        thumb = self.prepare_thumb(state)
        status = self.prepare_status(state)
        thumb_batch, status_batch = self.prepare_batch(thumb, status)
        out = self.model(thumb_batch, status_batch)

        # Prepare predictions from model output by converting them to numpy vector
        predictions = out.data.numpy()[0] if self.no_cuda else out.data.cpu().numpy()[0]

        # Normalize predictions to desired range
        for i, pd in enumerate(self.config['predict']):
            predictions[i] /= pd['scale']

        # Append frame to out buffer if we're writing
        if self.is_recording(state):
            self.out_buffer.append((state['timestamp'], thumb, predictions))

        return predictions


    def plan(self, state):
        # Do not do anything if we do not have a loaded model
        if self.model is None:
            return 0.0, 0.0

        # Get the predictions of our model
        predictions = self.predict(state)

        # Use the given speed and steer directly from predictions
        speed = float(predictions[0])
        steer = float(predictions[1])

        return speed, steer

    def record(self, state):

        # If we can not record, return false
        if not self.is_recording(state):
            return False

        # If we are initialized, then spit out jpg images directly to disk
        if not self.is_recording_initialized(state):
            super(Clone, self).record(state)
            self.folder = state['folder']
            self.recording_dir = os.path.join(self.folder, self.config['name'])
            self.frame_counter = 0
            os.mkdir(self.recording_dir)

        # Write out buffered images
        for timestamp, thumb, predictions in self.out_buffer:
            path = '%s/%06i.jpg' % (self.recording_dir, self.frame_counter)
            img = PIL.Image.fromarray(thumb)
            img.save(path)
            self.frame_counter += 1            
        del self.out_buffer[:]

        return True                         
