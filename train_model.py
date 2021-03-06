# Imports
#!pip install torchvision
import matplotlib
matplotlib.use('Agg')

import pandas as pd
import os
from sys import platform
import numpy as np
from numpy.distutils.misc_util import is_sequence
from bs4 import BeautifulSoup  # this is to extract info from the xml, if we use it in the end
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image
import json
import pickle

import torchvision
from torchvision import transforms, datasets, models
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch.optim as optim
from torch.utils.data.sampler import SubsetRandomSampler
from sklearn.metrics import f1_score, precision_score, recall_score
import statistics

import os
from datetime import datetime
from pathlib import Path
#import wandb

# Start a new run
# wandb.init(project='thermal_ped', entity='ncanna')

local_mode = True
iou_mode = True
rgb_mode = False

if platform == "win32":
    unix = False
else:
    unix = True

if local_mode:
    print(f"Local mode is: {local_mode}")

if iou_mode:
    print(f"IOU mode is: {iou_mode}")

if rgb_mode:
    print(f"RGB mode is: {rgb_mode}")

number_workers = 2 #min 8 cores
#number_workers = 4 #min 12 cores (16 better)
learning_rate = 0.001
weight_decay_rate =  0
early_stop = False

print(f"Learning rate is: {learning_rate}")
print(f"Weight decay is: {weight_decay_rate}")

save_epochs_every = True
save_epochs_num = 25

if save_epochs_every:
    print(f"Partial models will be saved every {save_epochs_num} epochs")

user = "n"

if user == "n":
    computing_id = "na3au"
elif user == "e":
    computing_id = "es3hd"
elif user == "s":
    computing_id = "sa3ag"

print(f"User is {computing_id}")

if local_mode:
    batch_size = 10
    num_epochs = 2
    selfcsv_df = pd.read_csv("frame_MasterList.csv").head(5)
    dir_path = os.getcwd()
    xml_ver_string = "xml"
else:
    batch_size = 1
    num_epochs = 50
    selfcsv_df = pd.read_csv("frame_MasterList.csv") #.head(50)
    dir_path = "/scratch/"+computing_id+"/modelRuns"
    xml_ver_string = "html.parser"

try:
    current_time = datetime.now().strftime("%Y_%m_%d-%I_%M_%S_%p")
    directory = dir_path + "/" + current_time + "_TRAINING"
    if not os.path.exists(directory):
        os.makedirs(directory)
    print(f'Creation of directory at {directory} successful')
except:
    print(f'Creation of directory at {directory} failed')
file_output_path = directory + "/"

# Get label and encode
def get_box(obj):
    xmin = float(obj.find('xmin').text)
    xmax = float(obj.find('xmax').text)
    ymin = float(obj.find('ymin').text)
    ymax = float(obj.find('ymax').text)
    return [xmin, ymin, xmax, ymax]

def get_label(obj):
    if obj.find('name').text == 'person' or obj.find('name').text == 'people':
        return 1
    if obj.find('name').text == 'cyclist':
        return 2
    else:
        return 0

# Generate the target location in the image
def generate_target(image_id, file):
    with open(file) as f:
        data = f.read()
        soup = BeautifulSoup(data, xml_ver_string)
        objects = soup.find_all('object')

        num_objs = len(objects)

        boxes = []
        labels = []

        for i in objects:
            boxes.append(get_box(i))
            labels.append(get_label(i))

        boxes = torch.as_tensor(boxes, dtype=torch.float32)
        labels = torch.as_tensor(labels, dtype=torch.int64)
        img_id = torch.tensor([image_id])

        # Creating the target for the box
        target = {}
        target['boxes'] = boxes
        target['labels'] = labels
        target['image_id'] = img_id

        return target

def OHE(label):
    if label == "People" or label == "Person":
        return 1
    elif label == "Cyclist":
        return 2
    else:
        return 0

def Recode(label):
    if label == 1:
        return "Person(s)"
    elif label == 2:
        return "Cyclist"
    else:
        return "N/A"

class FullImages(object):
    def __init__(self, transforms=None):
        self.csv = selfcsv_df
        self.csv_len = self.csv.shape[1]
        self.imgs = self.csv.image_path.tolist()
        self.imgs_len = len(self.imgs)
        self.transforms = transforms

    def __len__(self):
        return self.imgs_len
        # return self.csv_len

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        img = self.csv.loc[idx, 'image_path']
        annotation = self.csv.loc[idx, 'annotation_path']

        img = Image.open(img).convert("L")
        target = generate_target(idx, annotation)

        # label = self.labels[idx]
        # label = OHE(label)
        # label = torch.as_tensor(label, dtype=torch.int64)

        if self.transforms is not None:
            img = self.transforms(img)

        return img, target

# Normalize
if rgb_mode:
    data_transform = transforms.Compose([  # transforms.Resize((80,50)),
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5]), transforms.Lambda(lambda x: x.repeat(3, 1, 1))])
else:
    data_transform = transforms.Compose([  # transforms.Resize((80,50)),
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5])])

# Collate images
def collate_fn(batch):
    return tuple(zip(*batch))  # will need adjusting when pathing is adjusted

dataset = FullImages(data_transform)
data_size = len(dataset)
print(f'Length of Dataset: {data_size}')

indices = list(range(data_size))
test_split = 0.1
split = int(np.floor(test_split * data_size))
# print(f'Length of Split Dataset: {split}')

train_indices, test_indices = indices[split:], indices[:split]
len_train_ind, len_test_ind = len(train_indices), len(test_indices)
print(f'Length of Train: {len_train_ind}; Length of Test: {len_test_ind}')

train_sampler = SubsetRandomSampler(train_indices)
test_sampler = SubsetRandomSampler(test_indices)

data_loader = torch.utils.data.DataLoader(
    dataset,
    batch_size=batch_size,
    sampler=train_sampler,
    collate_fn=collate_fn,
    num_workers=number_workers
)

if iou_mode:
    test_data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        sampler=test_sampler,
        collate_fn=collate_fn,
        num_workers=number_workers
    )

len_dataloader = len(data_loader)
data_loader_test = torch.utils.data.DataLoader(dataset, batch_size=batch_size, sampler=test_sampler,
                                               collate_fn=collate_fn, num_workers=number_workers)
len_testdataloader = len(data_loader_test)
print(f'Length of Test: {len_testdataloader}; Length of Train: {len_dataloader}')

# Instance segmentation is crucial in using the full images
def get_model_instance_segmentation(num_classes):
    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=False)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(
        in_features, num_classes)
    for name, param in model.named_parameters():
        param.requires_grad_(True)
    return model

# print(f'{len_testdataloader} batches in test data loader.')
# print(f'{len_dataloader} batches in train data loader.')

# cnn = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained = False)
model = get_model_instance_segmentation(3)

# Check if GPU
cuda = torch.cuda.is_available()
if cuda:
    if torch.cuda.device_count() > 1:
        print("Let's use", torch.cuda.device_count(), "GPUs!")
        device = torch.device("cuda:0")
        model = nn.DataParallel(model)
    else:
        device = torch.device("cuda")
        print(f'Single CUDA.....baby shark doo doo doo')
else:
    device = torch.device("cpu")
    print(f'But I\'m just a poor CPU and nobody loves me :(')

def cluster_preds(num, ges, ann):
    annotation = ann[num]
    prediction = ges[num]
    annotation_boxes = annotation["boxes"].tolist()

    th_better = 0.3
    th_X = 2
    th_Y = 2

    ##### Original prediction boxes
    ix = 0
    voc_iou = []
    for box in prediction["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for bound in annotation_boxes:
            a_xmin, a_ymin, a_xmax, a_ymax = bound
            xA = max(xmin, a_xmin)
            yA = max(ymin, a_ymin)
            xB = min(xmax, a_xmax)
            yB = min(ymax, a_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            max_val_rounded = round(max(iou_list), 2)
            voc_iou.append(max_val)

        ix += 1

    ##### Calculate accuracy and IoU metrics
    prediction_mod = prediction["boxes"]
    ats_voc_iou_og = []
    for box in annotation["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for mod_box in prediction_mod:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_box.tolist()
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            ats_voc_iou_og.append(max_val)

    ##### Clustered prediction boxes
    # Collapse predictions
    prediction_mod = prediction["boxes"].tolist()
    subset_indices = []
    c_ix = 0
    for box in prediction["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        collapsed = False
        for compare_box in prediction["boxes"]:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box.tolist()

            if (xmin > mod_xmin) and (xmax < mod_xmax) and (ymin > mod_ymin) and (
                    ymax < mod_ymax) and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_superset = [[0, 0, 0, 0]]
    # prediction_mod = prediction["boxes"]
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box
        better_match = False

        prediction_mod_ix = 0
        for mod_pred in prediction_superset:
            if not better_match:
                mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_pred
                xA = max(xmin, mod_xmin)
                yA = max(ymin, mod_ymin)
                xB = min(xmax, mod_xmax)
                yB = min(ymax, mod_ymax)
                interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
                p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
                a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
                iou = interArea / float(p_area + a_area - interArea)

                if iou > th_better:
                    if (xmin + mod_xmin) / th_X < xmin:
                        xmin = (xmin + mod_xmin) / th_X
                    if (ymin + mod_ymin) / th_Y < ymin:
                        ymin = (ymin + mod_ymin) / th_Y
                    if (xmax + mod_xmax) / th_X > xmax:
                        xmax = (xmax + mod_xmax) / th_X
                    if (ymax + mod_ymax) / th_Y > ymax:
                        ymax = (ymax + mod_ymax) / th_Y

                    prediction_superset[prediction_mod_ix] = [xmin, ymin, xmax, ymax]
                    better_match = True
                    break

                prediction_mod_ix += 1

        if not better_match:
            prediction_superset.append([xmin, ymin, xmax, ymax])

    ##### SUPERSET
    prediction_mod = prediction_superset
    #print(prediction_superset)
    subset_indices = []
    c_ix = 0
    for box in prediction_superset:
        xmin, ymin, xmax, ymax = box

        collapsed = False
        for compare_box in prediction_superset:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box

            if (xmin > mod_xmin) and (xmax < mod_xmax) and (ymin > mod_ymin) and (
                    ymax < mod_ymax) and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_superset_clustered = [[0, 0, 0, 0]]
    # prediction_mod = prediction["boxes"]
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box
        better_match = False

        prediction_mod_ix = 0
        for mod_pred in prediction_superset_clustered:
            if not better_match:
                mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_pred
                xA = max(xmin, mod_xmin)
                yA = max(ymin, mod_ymin)
                xB = min(xmax, mod_xmax)
                yB = min(ymax, mod_ymax)
                interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
                p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
                a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
                iou = interArea / float(p_area + a_area - interArea)

                if iou > 0.1:
                    if (xmin + mod_xmin) / th_X < xmin:
                        xmin = (xmin + mod_xmin) / th_X
                    if (ymin + mod_ymin) / th_Y < ymin:
                        ymin = (ymin + mod_ymin) / th_Y
                    if (xmax + mod_xmax) / th_X > xmax:
                        xmax = (xmax + mod_xmax) / th_X
                    if (ymax + mod_ymax) / th_Y > ymax:
                        ymax = (ymax + mod_ymax) / th_Y

                    prediction_superset_clustered[prediction_mod_ix] = [xmin, ymin, xmax, ymax]
                    better_match = True
                    break

                prediction_mod_ix += 1

        if not better_match:
            prediction_superset_clustered.append([xmin, ymin, xmax, ymax])

    prediction_mod = prediction_superset_clustered
    subset_indices = []
    c_ix = 0
    for box in prediction_superset_clustered:
        xmin, ymin, xmax, ymax = box
        p_area = (xmax - xmin + 1) * (ymax - ymin + 1)

        collapsed = False
        for compare_box in prediction_superset_clustered:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area)

            if iou > 0.5 and iou != 1 and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_mod = prediction_mod[1:]

    ix = 0
    voc_iou_mod = []
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box

        iou_list = []
        for bound in annotation_boxes:
            a_xmin, a_ymin, a_xmax, a_ymax = bound
            xA = max(xmin, a_xmin)
            yA = max(ymin, a_ymin)
            xB = min(xmax, a_xmax)
            yB = min(ymax, a_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            voc_iou_mod.append(max_val)
        ix += 1

    ##### Calculate accuracy and IoU metrics
    ats_voc_iou_mod = []
    for box in annotation["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for mod_box in prediction_mod:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_box
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            ats_voc_iou_mod.append(max_val)

    return prediction_mod

def get_iou(num, ges, ann):
    annotation = ann[num]
    prediction = ges[num]
    annotation_boxes = annotation["boxes"].tolist()

    th_better = 0.3
    th_X = 2
    th_Y = 2

    ##### Original prediction boxes
    ix = 0
    voc_iou = []
    for box in prediction["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for bound in annotation_boxes:
            a_xmin, a_ymin, a_xmax, a_ymax = bound
            xA = max(xmin, a_xmin)
            yA = max(ymin, a_ymin)
            xB = min(xmax, a_xmax)
            yB = min(ymax, a_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            max_val_rounded = round(max(iou_list), 2)
            voc_iou.append(max_val)

        ix += 1

    ##### Calculate accuracy and IoU metrics
    prediction_mod = prediction["boxes"]
    ats_voc_iou_og = []
    for box in annotation["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for mod_box in prediction_mod:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_box.tolist()
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
             max_val = max(iou_list)
             ats_voc_iou_og.append(max_val)

    ##### Clustered prediction boxes
    # Collapse predictions
    prediction_mod = prediction["boxes"].tolist()
    subset_indices = []
    c_ix = 0
    for box in prediction["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        collapsed = False
        for compare_box in prediction["boxes"]:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box.tolist()

            if (xmin > mod_xmin) and (xmax < mod_xmax) and (ymin > mod_ymin) and (
                    ymax < mod_ymax) and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_superset = [[0, 0, 0, 0]]
    # prediction_mod = prediction["boxes"]
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box
        better_match = False

        prediction_mod_ix = 0
        for mod_pred in prediction_superset:
            if not better_match:
                mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_pred
                xA = max(xmin, mod_xmin)
                yA = max(ymin, mod_ymin)
                xB = min(xmax, mod_xmax)
                yB = min(ymax, mod_ymax)
                interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
                p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
                a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
                iou = interArea / float(p_area + a_area - interArea)

                if iou > th_better:
                    if (xmin + mod_xmin) / th_X < xmin:
                        xmin = (xmin + mod_xmin) / th_X
                    if (ymin + mod_ymin) / th_Y < ymin:
                        ymin = (ymin + mod_ymin) / th_Y
                    if (xmax + mod_xmax) / th_X > xmax:
                        xmax = (xmax + mod_xmax) / th_X
                    if (ymax + mod_ymax) / th_Y > ymax:
                        ymax = (ymax + mod_ymax) / th_Y

                    prediction_superset[prediction_mod_ix] = [xmin, ymin, xmax, ymax]
                    better_match = True
                    break

                prediction_mod_ix += 1

        if not better_match:
            prediction_superset.append([xmin, ymin, xmax, ymax])

    ##### SUPERSET
    prediction_mod = prediction_superset
    #print(prediction_superset)
    subset_indices = []
    c_ix = 0
    for box in prediction_superset:
        xmin, ymin, xmax, ymax = box

        collapsed = False
        for compare_box in prediction_superset:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box

            if (xmin > mod_xmin) and (xmax < mod_xmax) and (ymin > mod_ymin) and (
                    ymax < mod_ymax) and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_superset_clustered = [[0, 0, 0, 0]]
    # prediction_mod = prediction["boxes"]
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box
        better_match = False

        prediction_mod_ix = 0
        for mod_pred in prediction_superset_clustered:
            if not better_match:
                mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_pred
                xA = max(xmin, mod_xmin)
                yA = max(ymin, mod_ymin)
                xB = min(xmax, mod_xmax)
                yB = min(ymax, mod_ymax)
                interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
                p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
                a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
                iou = interArea / float(p_area + a_area - interArea)

                if iou > 0.8:
                    if (xmin + mod_xmin) / th_X < xmin:
                        xmin = (xmin + mod_xmin) / th_X
                    if (ymin + mod_ymin) / th_Y < ymin:
                        ymin = (ymin + mod_ymin) / th_Y
                    if (xmax + mod_xmax) / th_X > xmax:
                        xmax = (xmax + mod_xmax) / th_X
                    if (ymax + mod_ymax) / th_Y > ymax:
                        ymax = (ymax + mod_ymax) / th_Y

                    prediction_superset_clustered[prediction_mod_ix] = [xmin, ymin, xmax, ymax]
                    better_match = True
                    break

                prediction_mod_ix += 1

        if not better_match:
            prediction_superset_clustered.append([xmin, ymin, xmax, ymax])

    prediction_mod = prediction_superset_clustered
    subset_indices = []
    c_ix = 0
    for box in prediction_superset_clustered:
        xmin, ymin, xmax, ymax = box
        p_area = (xmax - xmin + 1) * (ymax - ymin + 1)

        collapsed = False
        for compare_box in prediction_superset_clustered:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = compare_box
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area)

            if iou > 0.8 and iou != 1 and not collapsed:
                subset_indices.append(c_ix)
                collapsed = True
                break
        c_ix += 1

    subset_indices.sort(reverse=True)
    for index_num in subset_indices:
        prediction_mod.pop(index_num)

    prediction_mod = prediction_mod[1:]

    ix = 0
    voc_iou_mod = []
    for box in prediction_mod:
        xmin, ymin, xmax, ymax = box

        iou_list = []
        for bound in annotation_boxes:
            a_xmin, a_ymin, a_xmax, a_ymax = bound
            xA = max(xmin, a_xmin)
            yA = max(ymin, a_ymin)
            xB = min(xmax, a_xmax)
            yB = min(ymax, a_ymax)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            voc_iou_mod.append(max_val)
        ix += 1

    ##### Calculate accuracy and IoU metrics
    ats_voc_iou_mod = []
    for box in annotation["boxes"]:
        xmin, ymin, xmax, ymax = box.tolist()

        iou_list = []
        for mod_box in prediction_mod:
            mod_xmin, mod_ymin, mod_xmax, mod_ymax = mod_box
            xA = max(xmin, mod_xmin)
            yA = max(ymin, mod_ymin)
            xB = min(xmax, mod_xmax)
            yB = min(ymax, mod_ymax)
            p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
            a_area = (mod_xmax - mod_xmin + 1) * (mod_ymax - mod_ymin + 1)
            interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
            iou = interArea / float(p_area + a_area - interArea)
            iou_list.append(iou)

        if len(iou_list) != 0:
            max_val = max(iou_list)
            ats_voc_iou_mod.append(max_val)

    #print("\n Original Predictions")
    #print(
    #    f'{len(prediction["boxes"])} boxes made for {len(annotation["boxes"])} actual boxes in {str(output_name)} for {identifier} with note {input} (INDEX {num})')
    if len(voc_iou) == 0:
        mean_iou = 0
        #print(f'No predictions made so Mean IOU: {mean_iou}')
    else:
        og_mean_iou = sum(voc_iou) / len(voc_iou)
        fp = voc_iou.count(0)
        bp = sum((i > 0 and i < 0.4) for i in voc_iou)
        gp = sum((i >= 0.4) for i in voc_iou)
        og_accuracy = sum([1 if entry >= 0.4 else 0 for entry in ats_voc_iou_og]) / len(annotation["boxes"])
        '''print(f'{fp} false positives (IOU = 0)')
        print(f'{bp} bad positives (0 < IOU < 0.4)')
        print(f'{gp} good positives (IOU >= 0.4)')
        print(f'Mean IOU: {mean_iou}')
        print(f'Accuracy: {accuracy*100}%')
        # print(f'Predictions for Image {num} have mean IOU: {mean_iou} and accuracy: {accuracy}')'''

    #print("\n Clustered Predictions")
    if len(voc_iou_mod) == 0:
        mean_iou = 0
        #print(f'No predictions made so Mean IOU: {mean_iou}')
    else:
        mean_iou = sum(voc_iou_mod) / len(voc_iou_mod)
        fp = voc_iou_mod.count(0)
        bp = sum((i > 0 and i < 0.4) for i in voc_iou_mod)
        gp = sum((i >= 0.4) for i in voc_iou_mod)
        accuracy = sum([1 if entry >= 0.4 else 0 for entry in ats_voc_iou_mod]) / len(
            annotation["boxes"])
        '''print(
            f'{len(prediction_mod)} boxes made for {len(annotation["boxes"])} actual boxes in {str(output_name)} for {identifier} with note {input} (INDEX {num})')
        print(f'{fp} false positives (IOU = 0)')
        print(f'{bp} bad positives (0 < IOU < 0.4)')
        print(f'{gp} good positives (IOU >= 0.4)')
        print(f'Mean IOU: {mean_iou}')
        print(f'Accuracy: {accuracy*100}%')
        # print(f'Predictions for Image {num} have mean IOU: {mean_iou} and accuracy: {accuracy}')'''

    return [accuracy, mean_iou, og_accuracy, og_mean_iou]

model.to(device)
params = [p for p in model.parameters() if p.requires_grad]
optimizer = torch.optim.Adam(params, lr = learning_rate, weight_decay = weight_decay_rate)

tot_ats = 0
epochs = 0

epoch_iou_list = []
epoch_acc_list = []
epoch_losses = []

save_epoch = False
lr_threshold = 0.001

#wandb.watch(model)
for epoch in range(num_epochs):

    epochs += 1

    print(f'Epoch: {epochs}')

    model.train()

    epoch_loss = 0
    epoch_iou = 0

    i = 0

    for train_imgs, train_annotations in data_loader:
        # torch.cuda.empty_cache()
        imgs = list(img.to(device) for img in train_imgs)
        annotations = [{k: v.to(device) for k, v in t.items()} for t in train_annotations]

        loss_dict = model(imgs, annotations)
        losses = sum(loss for loss in loss_dict.values())

            # for param_group in optimizer.param_groups:
            #     if param_group['lr'] < lr_threshold:
            #         early_stop = False
            #
            #     if not early_stop:
            #         # print(f"Learning rate for epoch {epoch} is {param_group['lr']}")
            #     else:
            #         save_epoch = True

        optimizer.zero_grad()
        losses.backward()
        optimizer.step()

        i += 1
        tot_ats += 1

        epoch_loss += losses.item()

        print(f'Iteration Number: {i}/{len_dataloader}, Loss: {losses}')

    for train_imgs, train_annotations in data_loader:
        imgs = list(img.to(device) for img in train_imgs)
        annotations = [{k: v.to(device) for k, v in t.items()} for t in train_annotations]
        loss_dict = model([imgs[0]], [annotations[0]])
        losses = sum(loss for loss in loss_dict.values())

        optimizer.zero_grad()

        '''for param_group in optimizer.param_groups:
            if param_group['lr'] < lr_threshold:
                early_stop = True

            if not early_stop:
                pass
                #print(f"Learning rate for epoch {epoch} is {param_group['lr']}")
            else:
                save_epoch = True'''

        losses.backward()
        optimizer.step()

        i += 1
        tot_ats += 1

        epoch_loss += losses.item()

        print(f'Iteration Number: {i}/{len_dataloader}, Loss: {losses}')

    mean_epoch_loss = epoch_loss / i
    epoch_losses.append(mean_epoch_loss)
    #wandb.log({'loss': mean_epoch_loss})

    # Epoch-wise Training IoU
    try:
        if iou_mode:
            model.eval()
            with torch.no_grad():
                for test_imgs, test_annotations in data_loader_test:
                    imgs_test = list(img_test.to(device) for img_test in test_imgs)
                    annotations_test = [{k: v.to(device) for k, v in t.items()} for t in test_annotations]

                guess = model(imgs_test)
                epoch_iou = get_iou(0, guess, annotations_test)

                model.train()

                epoch_acc = epoch_iou[0]
                epoch_avg = epoch_iou[1]
                epoch_iou_list.append(epoch_avg)
                epoch_acc_list.append(epoch_acc)
            print(f"Epoch {epochs} IoU: ", epoch_avg)
    except Exception as e:
        print(e)
        epoch_iou_list.append("Exception")
        pass

    if save_epochs_every and epochs % save_epochs_num == 0:
        if iou_mode:
            df = pd.DataFrame({'Mean_Epoch_Loss': epoch_losses, 'Mean_Training_IOU': epoch_iou_list, 'Mean Accuracy': epoch_acc_list})
        else:
            df = pd.DataFrame({'Mean_Epoch_Loss': epoch_losses})

        partial_name = "partial_model_" + str(epochs)

        try:
            # Save model
            torch.save(model.state_dict(), file_output_path + partial_name + ".pt")
            print(f'Partial model trained on {epochs} epochs saved to {directory}.')
        except:
            print(f'Could not save partial model at epoch {epochs}.')
            pass

        try:
            # Save training metrics
            df.to_csv(file_output_path + partial_name + "_losses.csv", index=False)
            print(f'Partial model metrics trained on {epochs} epochs saved to {directory}.')
        except:
            print(f'Could not save partial model metrics at epoch {epochs}.')
            pass

try:
    # Save training metrics
    full_name = "full_model_losses_" + str(epochs) + ".csv"

    if iou_mode:
        df = pd.DataFrame({'Mean_Epoch_Loss': epoch_losses, 'Mean_Training_IOU': epoch_iou_list, 'Mean Accuracy': epoch_acc_list})
    else:
        df = pd.DataFrame({'Mean_Epoch_Loss': epoch_losses})

    df.to_csv(file_output_path + full_name, index=False)
    print(f'Full model losses for {epochs} epochs saved to {directory}.')
except:
    pass

try:
    # Save model
    torch.save(model.state_dict(), file_output_path + 'full_model.pt')
    print(f'Full model trained on {epochs} epochs saved to {directory}.')
except:
    pass

#print(f'Annotations Trained: {tot_ats}')
#print(epoch_ats)

# for train_imgs, train_annotations in data_loader:
#     imgs_train = list(img_train.to(device) for img_train in train_imgs)
#     annotations_train = [{k: v.to(device) for k, v in t.items()} for t in train_annotations]

# imgs_train = [t.to(device) for t in imgs_train]
# imgs_test = [t.to(device) for t in imgs_test]

# train_annotations = [{'boxes': d['boxes'].to(device), 'labels': d['labels'].to(device),'image_id': d['image_id'].to(device)} for d in train_annotations]
# test_annotations = [{'boxes': d['boxes'].to(device), 'labels': d['labels'].to(device),'image_id': d['image_id'].to(device)} for d in test_annotations]
#
# master_csv = pd.read_csv("frame_MasterList.csv")
# model.eval()

# print("Evaluation Phase Started")
# print("Train predictions")
# preds_train = model(imgs_train)
# print(preds_train[0])
#
# print("Test predictions")
# preds_test = model(imgs_test)
# print(preds_test[0])
#
# print(f' Train ats: {len(train_annotations)}; test ats: {len(test_annotations)}')

# try:
#     if preds_train == preds_test:
#         print(f'Train predictions EQUAL test predictions.')
#     else:
#         print(f'Train predictions DO NOT EQUAL test predictions.')
# except:
#     pass

# def get_iou(num, input, test=False):
#     if test:
#         identifier = "Test"
#         annotation = annotations_test[num]
#         prediction = preds_test[num]
#     else:
#         identifier = "Train"
#         annotation = annotations[num]
#         prediction = preds_train[num]
#
#     annotation_boxes = annotation["boxes"].tolist()
#
#     ix = 0
#     for box in annotation["boxes"]:
#         img_id = annotation["image_id"].item()
#         file_name = master_csv.loc[img_id, :].image_path
#         set = file_name.split("/")[7]
#         video = file_name.split("/")[8]
#         file_name = file_name.split("/")[10]
#         file_name = file_name[:-4]
#         output_name = set + "_" + video + "_" + file_name
#         ix += 1
#
#     ix = 0
#     voc_iou = []
#     #print(f'{len(prediction["boxes"])} prediction boxes made for {len(annotation["boxes"])}
#     # actual boxes in {str(output_name)} for {identifier} with note {input}')
#     for box in prediction["boxes"]:
#         xmin, ymin, xmax, ymax = box.tolist()
#         iou_list = []
#         for bound in annotation_boxes:
#             a_xmin, a_ymin, a_xmax, a_ymax = bound
#             xA = max(xmin, a_xmin)
#             yA = max(ymin, a_ymin)
#             xB = min(xmax, a_xmax)
#             yB = min(ymax, a_ymax)
#             interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
#             p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
#             a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
#             iou = interArea / float(p_area + a_area - interArea)
#             iou_list.append(iou)
#         max_val = max(iou_list)
#         voc_iou.append(max_val)
#         ix += 1
#
#     if len(voc_iou) == 0:
#         mean_iou = 0
#         print(f'No predictions made so Mean IOU: {mean_iou}')
#     else:
#         mean_iou = sum(voc_iou) / len(voc_iou)
#
#     return [mean_iou, voc_iou]
#
#
# def plot_images(num, input):
#     fig, ax = plt.subplots(nrows=1, ncols=2)
#     img_tensor = imgs[num]
#     annotation = annotations[num]
#     # for key, value in annotation.items():
#     #         print(key, value)
#     prediction = preds_train[num]
#
#     img = img_tensor.cpu().data
#     img = img[0, :, :]
#
#     ax[0].imshow(img, cmap='gray')
#     ax[1].imshow(img, cmap='gray')
#
#     ix = 0
#     for box in annotation["boxes"]:
#         # print(annotations[ix])
#         xmin, ymin, xmax, ymax = box.tolist()
#         value = annotation["labels"][ix]
#         img_id = annotation["image_id"].item()
#         file_name = master_csv.loc[img_id, :].image_path
#         set = file_name.split("/")[7]
#         video = file_name.split("/")[8]
#         file_name = file_name.split("/")[10]
#         file_name = file_name[:-4]
#         output_name = set + "_" + video + "_" + file_name
#         text = Recode(value)
#         colors = ["r", "#00FF00", "#0000FF"]
#         rect = patches.Rectangle((xmin, ymin), (xmax - xmin), (ymax - ymin), linewidth=1,
#                                  edgecolor=colors[value], facecolor='none')
#         target_x = xmin
#         target_y = ymin - 5
#         ax[0].text(target_x, target_y, text, color=colors[value])
#         ax[0].add_patch(rect)
#         ix += 1
#
#     ix = 0
#     print(str(len(prediction["boxes"])) + " prediction boxes made for " + str(
#         len(annotation["boxes"])) + " actual boxes in " + str(output_name))
#     for box in prediction["boxes"]:
#         xmin, ymin, xmax, ymax = box.tolist()
#         value = prediction["labels"][ix]
#         text = Recode(value)
#         colors = ["r", "#00FF00", "#0000FF"]
#         rect = patches.Rectangle((xmin, ymin), (xmax - xmin), (ymax - ymin), linewidth=1,
#                                  edgecolor=colors[value], facecolor='none')
#         target_x = xmin
#         target_y = ymin - 5
#         ax[1].text(target_x, target_y, text, color=colors[value])
#         ax[1].add_patch(rect)
#         ix += 1
#
#     # figname = file_name+"_"+input+".png"
#     # fig.savefig(figname)
#     if local_mode:
#         plt.show()
#
# def plot_iou(num, input, test=False):
#     fig, ax = plt.subplots(1)
#     if test:
#         identifier = "Test"
#         print(identifier)
#         img_tensor = imgs_test[num]
#         annotation = annotations_test[num]
#         prediction = preds_test[num]
#     else:
#         identifier = "Train"
#         print(identifier)
#         img_tensor = imgs[num]
#         annotation = annotations[num]
#         prediction = preds_train[num]
#
#     img = img_tensor.cpu().data
#     img = img[0, :, :]
#     annotation_boxes = annotation["boxes"].tolist()
#
#     if local_mode:
#         ax.imshow(img, cmap='gray')
#
#     ix = 0
#     for box in annotation["boxes"]:
#         xmin, ymin, xmax, ymax = box.tolist()
#         value = annotation["labels"][ix]
#         img_id = annotation["image_id"].item()
#         file_name = master_csv.loc[img_id, :].image_path
#         set = file_name.split("/")[7]
#         video = file_name.split("/")[8]
#         file_name = file_name.split("/")[10]
#         file_name = file_name[:-4]
#         output_name = set + "_" + video + "_" + file_name + "_" + identifier
#         text = Recode(value)
#         colors = ["r", "r", "r"]
#         rect = patches.Rectangle((xmin, ymin), (xmax - xmin), (ymax - ymin), linewidth=1,
#                                  edgecolor=colors[value], facecolor='none')
#         target_x = xmin
#         target_y = ymin - 5
#         ax.text(target_x, target_y, text, color=colors[value])
#         ax.add_patch(rect)
#         ix += 1
#
#     ix = 0
#     voc_iou = []
#     print(
#         f'{len(prediction["boxes"])} prediction boxes made for {len(annotation["boxes"])} actual boxes in {str(output_name)} for {identifier} with note {input} (INDEX {num})')
#     for box in prediction["boxes"]:
#         xmin, ymin, xmax, ymax = box.tolist()
#
#         iou_list = []
#         for bound in annotation_boxes:
#             a_xmin, a_ymin, a_xmax, a_ymax = bound
#             xA = max(xmin, a_xmin)
#             yA = max(ymin, a_ymin)
#             xB = min(xmax, a_xmax)
#             yB = min(ymax, a_ymax)
#             interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
#             p_area = (xmax - xmin + 1) * (ymax - ymin + 1)
#             a_area = (a_xmax - a_xmin + 1) * (a_ymax - a_ymin + 1)
#             iou = interArea / float(p_area + a_area - interArea)
#             iou_list.append(iou)
#         max_val = max(iou_list)
#         voc_iou.append(max_val)
#
#         max_ix = iou_list.index(max_val)
#         map_dict = {max_ix: max_val}
#
#         # iou_string = ', '.join((str(float) for float in iou_list))
#         value = prediction["labels"][ix]
#         text = json.dumps(map_dict)
#         colors = ["r", "#00FF00", "#0000FF"]
#         rect = patches.Rectangle((xmin, ymin), (xmax - xmin), (ymax - ymin), linewidth=1,
#                                  edgecolor=colors[value], facecolor='none')
#         target_x = xmin
#         target_y = ymin - 5
#         ax.text(target_x, target_y, text, color=colors[value])
#         ax.add_patch(rect)
#         ix += 1
#
#     if local_mode:
#         plt.show()
#
#     if len(voc_iou) == 0:
#         mean_iou = 0
#         print(f'No predictions made so Mean IOU: {mean_iou}')
#     else:
#         mean_iou = sum(voc_iou) / len(voc_iou)
#         fp = voc_iou.count(0) / len(voc_iou) * 100
#         bp = sum((i > 0 and i < 0.5) for i in voc_iou) / len(voc_iou) * 100
#         gp = sum((i >= 0.5) for i in voc_iou) / len(voc_iou) * 100
#         print(f'{fp} false positives (IOU = 0)')
#         print(f'{bp} bad positives (0 < IOU < 0.5)')
#         print(f'{gp} good positives (IOU >= 0.5)')
#         print(f'Mean IOU: {mean_iou}')
#
#     figname = output_name + "_" + input + ".png"
#     fig.savefig(file_output_path + figname)
#     #print(f'Figure {figname} saved to {directory}.')
#
# print(f'Train is {len(preds_train)} and test is {len(preds_test)}')

#plot_images(0, "first")
# plot_iou(0, "first", False)
# plot_iou(len(preds_train) - 1, "last", False)
# get_iou(len(preds_train) - 1, "last", False)[0]
#
# plot_iou(0, "first", True)
# plot_iou(len(preds_test) - 1, "last", True)
# get_iou(len(preds_test) - 1, "last", True)[0]

# iou_df_train = pd.DataFrame(columns=["Train_Mean_IOU", "IOU_List"])
# iou_df_train_name = "full_iou_TRAIN_" + str(epochs) + ".csv"
# for train_pred in range(0, len(preds_train)):
#     iou_function = get_iou(train_pred, "first", False)
#     len_df = len(iou_df_train)
#     iou_df_train.loc[len_df, :] = iou_function
#     try:
#         if train_pred % 50 == 0:
#             partial_name = "partial_iou_TRAIN_" + str(train_pred) + "_images.csv"
#             iou_df_train.to_csv(file_output_path + iou_df_train_name, index=False)
#             print(f'Partial train IOUs for {len(iou_df_train)} images saved to {directory}.')
#     except:
#         pass
#
# iou_df_train.to_csv(file_output_path + iou_df_train_name, index=False)
# print(f'Full train IOUs for {len(iou_df_train)} images saved to {directory}.')
# print(iou_df_train.sort_values(by='Train_Mean_IOU', ascending=False).head(5))
#
# iou_df_test = pd.DataFrame(columns=["Test_Mean_IOU", "IOU_List"])
# iou_df_test_name = "full_iou_TEST_" + str(epochs) + ".csv"
# for test_pred in range(0, len(preds_test)):
#     iou_function = get_iou(test_pred, "test", False)
#     len_df = len(iou_df_test)
#     iou_df_test.loc[len_df, :] = iou_function
#     try:
#         if test_pred % 50 == 0:
#             partial_name = "partial_iou_TEST_" + str(test_pred) + "_images.csv"
#             iou_df_test.to_csv(file_output_path + iou_df_test_name, index=False)
#             print(f'Partial test IOUs for {len(iou_df_test)} images saved to {directory}.')
#     except:
#         pass
#
# iou_df_test.to_csv(file_output_path + iou_df_test_name, index=False)
# print(f'Full test IOUs for {len(iou_df_test)} images saved to {directory}.')
# print(iou_df_test.sort_values(by='Test_Mean_IOU', ascending=False).head(5))
#
# max_train_ix = iou_df_train[iou_df_train['Train_Mean_IOU'] == iou_df_train['Train_Mean_IOU'].max()].index.tolist()[0]
# max_test_ix = iou_df_test[iou_df_test['Test_Mean_IOU'] == iou_df_test['Test_Mean_IOU'].max()].index.tolist()[0]
#
# if local_mode:
#     plot_iou(max_train_ix, "best", False)
#     plot_iou(max_test_ix, "best", True)
#
# print(f'Train Mean IOU: {iou_df_train["Train_Mean_IOU"].mean()}')
# print(f'Test Mean IOU: {iou_df_test["Test_Mean_IOU"].mean()}')