#!/usr/bin/env python3
import os
import cv2
import tensorflow as tf
import numpy as np
from keras.models import model_from_json, model_from_yaml

'''
Defines the model class containing the following functions:
    __init__
    __del__
    evaluate
    roadspotter
    road_mapper
    pilot_mk1
'''

import yaml
with open("config/line_model.yaml", 'r') as yamlfile:
                lm_cfg = yaml.load(yamlfile)
with open("config/paras.yaml", 'r') as yamlfile:
                car_cfg = yaml.load(yamlfile)

class Model:
        
    def __init__(self, log, model_path, weights_path):
        """
        Open the model
        """
        self.log = log
        self.model_path = model_path
        self.weights_path = weights_path

        '''
        self.source_size = (640, 480)
        self.crop_size = (640, 160)
        self.crop_x = 0
        self.crop_y = self.source_size[1] - self.crop_size[1]
        self.target_size = (128, 32)
        '''

        # Line Model input characteristics:
        self.source_size = (car_cfg['record']['width'], 
                            car_cfg['record']['height'])
        self.crop_size = (  lm_cfg['line']['cropped_width'],
                            lm_cfg['line']['cropped_height'] )
        self.target_size = (lm_cfg['line']['input_width'] , 
                            lm_cfg['line']['input_height'])

        self.crop_x = int( (self.source_size[0] - self.crop_size[0] ) /2 )
        self.crop_y = self.source_size[1] - self.crop_size[1] 
        
        if model_path is not None and weights_path is not None:
            with open(model_path) as f:
                json_contents = f.read()
            self.model = model_from_yaml(json_contents)
            self.model.load_weights(weights_path)

        #define model output characteristics:
        self.n_lines = lm_cfg['line']['n_lines']
        self.n_points = lm_cfg['line']['n_points']
        self.n_dimensions = lm_cfg['line']['n_dimensions']

        #define camera characteristics
        #linear measurements given in mm
        self.camera_height = 380
        self.camera_min_view = 500 #Fixme remeasure distance
        #arcs measured in radians
        self.camera_to_ground_arc = np.arctan(self.camera_min_view / self.camera_height)
        self.camera_offset_y = 0
        self.camera_arc_y = 80 * (np.pi / 180)
        self.camera_arc_x = 60 * (np.pi / 180)
        self.crop_ratio = [c / s for c, s in zip(self.crop_size, self.source_size)]

            
    def __del__(self):
        """
        Deconstructor to close file objects
        """
        pass

    def preprocess(self, example):
        patch = example[self.crop_y : self.crop_y + self.crop_size[1],
                                        self.crop_x : self.crop_x + self.crop_size[0], :]
        thumb = cv2.resize(patch, self.target_size, interpolation=cv2.INTER_AREA)
        batch = np.reshape(thumb, [1] + list(thumb.shape))
        return batch
                            
    #Runs clonemodel
    #seriously Karol, you couldn't keep the variable order consistant comming in and out of this function?
    def evaluate(self, frame, speed, steer):
        """ 
        Cut out the patch and run the model on it
        """
        batch = self.preprocess(frame)
        nn_speed, nn_steer = self.model.predict_on_batch(batch)[0]
        return nn_speed, nn_steer, batch[0]

    #extracts frames from video for use by the validation function
    #this allows us to validate the model with real world images instead of simulated images.
    def video_to_frames(self, folder="data/20170812T214343Z-paras",
             max_frames=256, edge_detect=1, channels_out=1):
        # Prepare video frames by extracting the patch and thumbnail for training
        video_path = os.path.join(folder, 'camera_front.mp4')
        print(video_path)
        video_cap = cv2.VideoCapture(video_path)

        #initializing the car's perspective
        #viewer = Model(None, None, None)

        #initializing the output array
        frames = np.zeros([max_frames, self.target_size[1] , self.target_size[0],
                    channels_out], dtype=np.uint8)

        counter = 0
        while video_cap.isOpened() and counter < max_frames:
            # Get the frame
            ret, frame = video_cap.read()
            if not ret: break

            prepared = self.preprocess(frame)[0]
            channel = prepared.shape[2]
            if channels_out==1:
                prepared = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
            if edge_detect:
                prepared = cv2.flip(prepared, 0)
                prepared = cv2.Canny(prepared,100,200)
                prepared[prepared < 128] = 0
                prepared[prepared >= 128] = 255
            prepared = np.reshape(prepared, (lm_cfg['line']['input_height'] , 
                                            lm_cfg['line']['input_width'] ,channels_out))
            frames[counter] = prepared
            counter += 1
 
        #cleanup      
        video_cap.release()

        # Return our batch
        return frames


    #runs line_model returns camera pov bezier control points
    #OOM error will occur if too large a batch is passed in through frame
    def road_spotter(self, frame):

        #frame = (frame.astype(float) - np.mean(frame, dtype=float))/ 
        #        np.std(frame, dtype=float)
        road_lines = self.model.predict(frame)

        return np.reshape(road_lines, (frame.shape[0], self.n_lines, self.n_dimensions, self.n_points) )

    #This function uses a transform to map the percieved road onto a 2d plane beneath the car
    def road_mapper(self, frame):

        road_spots = self.road_spotter(frame)

        #road_map = np.zeros(np.shape(road_spots), np.float)

        #First we deal with all the points
        road_map[:, 1, :] = self.camera_height * np.tan(self.camera_to_ground_arc + 
                    road_spots[:, 0, :] * self.camera_arc_x/self.crop_ratio[1])
        road_map[:, 0, :] = np.multiply( np.power( ( np.power(self.camera_height, 2) +
                     np.power(road_map[:, 1, :], 2) ), 0.5 ) , np.tan(self.camera_offset_y +
                      (road_spots[:, 1, :]-0.5)*self.camera_arc_y ) )

        return road_map

    # pilot_mk1 is currently only built for low speed operation
    def pilot_mk1(self, frame):

        batch = self.preprocess(frame)

        road_map = self.road_mapper( batch)

        #speed is a constant, for now
        nn_speed = .25

        #steering angle is a function of how far the car believes it is from the center of the road
        #note that this is completely un damped and may become unstable at high speeds
        nn_steer = road_map[1, 0, 0] + (road_map[2, 0, 0] + road_map[0, 0, 0])/2
        
        # center vector is a measure of what direction the road is pointing
        center_vector = road_map[1, :, 1] - road_map[1, :, 0]
        '''
        road angle compensation
        adjusts stearing angle to account for road curvature
        nn_speed term is a linear scaling factor to kill this component at low speeds
        where position correction should be sufficient without a differential component
        (note that the main issue with high speed operation is lag between camera and wheels)
        '''
        nn_steer = nn_steer + 2 * nn_speed * center_vector[0]/center_vector[1]

        return nn_speed, nn_steer, batch[0]
