# -*-coding:utf-8-*-
import argparse
import math
# import h5py
import numpy as np
import random
import tensorflow as tf
# import socket
import importlib
import os
import sys
from sklearn.decomposition import PCA
from scipy import spatial

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'models'))
sys.path.append(os.path.join(BASE_DIR, 'utils'))
# import provider
import tf_util

parser = argparse.ArgumentParser()
parser.add_argument('--gpu', type=int, default=0, help='GPU to use [default: GPU 0]')
parser.add_argument('--model', default='pointnet_cls_basic', help='Model name: pointnet_cls or pointnet_cls_basic [default: pointnet_cls]')
parser.add_argument('--log_dir', default='log', help='Log dir [default: log]')
# parser.add_argument('--num_point', type=int, default=1024, help='Point Number [256/512/1024/2048] [default: 1024]')
parser.add_argument('--max_epoch', type=int, default=30, help='Epoch to run [default: 50]')
parser.add_argument('--batch_size', type=int, default=1, help='Batch Size during training [default: 32]')
parser.add_argument('--learning_rate', type=float, default=0.0002, help='Initial learning rate [default: 0.001]')
parser.add_argument('--momentum', type=float, default=0.9, help='Initial learning rate [default: 0.9]')
parser.add_argument('--optimizer', default='adam', help='adam or momentum [default: adam]')
parser.add_argument('--decay_step', type=int, default=200000, help='Decay step for lr decay [default: 200000]')
parser.add_argument('--decay_rate', type=float, default=0.7, help='Decay rate for lr decay [default: 0.8]')
FLAGS = parser.parse_args()


BATCH_SIZE = FLAGS.batch_size
# NUM_POINT = FLAGS.num_point
MAX_EPOCH = FLAGS.max_epoch
BASE_LEARNING_RATE = FLAGS.learning_rate
GPU_INDEX = FLAGS.gpu
MOMENTUM = FLAGS.momentum
OPTIMIZER = FLAGS.optimizer
DECAY_STEP = FLAGS.decay_step
DECAY_RATE = FLAGS.decay_rate

MODEL = importlib.import_module(FLAGS.model) # import network module
MODEL_FILE = os.path.join(BASE_DIR, 'models', FLAGS.model+'.py')
LOG_DIR = FLAGS.log_dir
if not os.path.exists(LOG_DIR): os.mkdir(LOG_DIR)
os.system('cp %s %s' % (MODEL_FILE, LOG_DIR)) # bkp of model def
os.system('cp train.py %s' % (LOG_DIR)) # bkp of train procedure
LOG_FOUT = open(os.path.join(LOG_DIR, 'log_train.txt'), 'w')
LOG_FOUT.write(str(FLAGS)+'\n')

MAX_NUM_POINT = 2048
NUM_CLASSES = 5

BN_INIT_DECAY = 0.5
BN_DECAY_DECAY_RATE = 0.5
BN_DECAY_DECAY_STEP = float(DECAY_STEP)
BN_DECAY_CLIP = 0.99

# HOSTNAME = socket.gethostname()

# # ModelNet40 official train/test split
# TRAIN_FILES = provider.getDataFiles( \
#     os.path.join(BASE_DIR, 'data/modelnet40_ply_hdf5_2048/train_files.txt'))
# TEST_FILES = provider.getDataFiles(\
#     os.path.join(BASE_DIR, 'data/modelnet40_ply_hdf5_2048/test_files.txt'))

def log_string(out_str):
    LOG_FOUT.write(out_str+'\n')
    LOG_FOUT.flush()
    print(out_str)


def get_learning_rate(batch):
    learning_rate = tf.train.exponential_decay(
                        BASE_LEARNING_RATE,  # Base learning rate.
                        batch * BATCH_SIZE,  # Current index into the dataset.
                        DECAY_STEP,          # Decay step.
                        DECAY_RATE,          # Decay rate.
                        staircase=True)
    learning_rate = tf.maximum(learning_rate, 0.00001) # CLIP THE LEARNING RATE!
    return learning_rate        

def get_bn_decay(batch):
    bn_momentum = tf.train.exponential_decay(
                      BN_INIT_DECAY,
                      batch*BATCH_SIZE,
                      BN_DECAY_DECAY_STEP,
                      BN_DECAY_DECAY_RATE,
                      staircase=True)
    bn_decay = tf.minimum(BN_DECAY_CLIP, 1 - bn_momentum)
    return bn_decay

def scale_features(data, ax):
    # return (data - np.mean(data, axis=ax)) / np.std(data, axis=ax)
    # return (data - np.mean(data, axis=ax))
    pca = PCA(n_components=2, copy=False)
    data[:, 0:2] = pca.fit_transform(data[:, 0:2])
    data[:, :-1] = (data[:, :-1] - np.mean(data[:, :-1], axis=ax)) / np.array([11.344, 2.2207, 1.4886])
    data[:, -1] = data[:, -1] / np.array([0.99])
    return data

def train():
    with tf.Graph().as_default():
        with tf.device('/gpu:'+str(GPU_INDEX)):
            pointclouds_pl, labels_pl = MODEL.placeholder_inputs(BATCH_SIZE)
            is_training_pl = tf.placeholder(tf.bool, shape=())
            # num_point_pl = tf.placeholder(tf.int32, shape=())
            print(is_training_pl)
            
            # Note the global_step=batch parameter to minimize. 
            # That tells the optimizer to helpfully increment the 'batch' parameter for you every time it trains.
            batch = tf.Variable(0)
            bn_decay = get_bn_decay(batch)
            tf.summary.scalar('bn_decay', bn_decay)

            # Get model and loss 
            pred, end_points = MODEL.get_model(pointclouds_pl, is_training_pl, bn_decay=bn_decay)
            loss = MODEL.get_loss(pred, labels_pl, end_points)
            tf.summary.scalar('loss', loss)

            correct = tf.equal(tf.argmax(pred, 1), tf.to_int64(labels_pl))
            accuracy = tf.reduce_sum(tf.cast(correct, tf.float32)) / float(BATCH_SIZE)
            tf.summary.scalar('accuracy', accuracy)

            # Get training operator
            learning_rate = get_learning_rate(batch)
            tf.summary.scalar('learning_rate', learning_rate)
            if OPTIMIZER == 'momentum':
                optimizer = tf.train.MomentumOptimizer(learning_rate, momentum=MOMENTUM)
            elif OPTIMIZER == 'adam':
                optimizer = tf.train.AdamOptimizer(learning_rate)
            train_op = optimizer.minimize(loss, global_step=batch)
            
            # Add ops to save and restore all the variables.
            saver = tf.train.Saver()
            
        # Create a session
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        config.log_device_placement = False
        sess = tf.Session(config=config)

        # Add summary writers
        #merged = tf.merge_all_summaries()
        merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter(os.path.join(LOG_DIR, 'train'),
                                  sess.graph)
        test_writer = tf.summary.FileWriter(os.path.join(LOG_DIR, 'test'))

        # Init variables
        init = tf.global_variables_initializer()
        # To fix the bug introduced in TF 0.12.1 as in
        # http://stackoverflow.com/questions/41543774/invalidargumenterror-for-tensor-bool-tensorflow-0-12-1
        #sess.run(init)
        sess.run(init, {is_training_pl: True})

        ops = {'pointclouds_pl': pointclouds_pl,
                # 'num_point_pl': num_point_pl,
               'labels_pl': labels_pl,
               'is_training_pl': is_training_pl,
               'pred': pred,
               'loss': loss,
               'train_op': train_op,
               'merged': merged,
               'step': batch}

        for epoch in range(MAX_EPOCH):
            log_string('**** EPOCH %03d ****' % (epoch))
            sys.stdout.flush()
             
            train_one_epoch(sess, ops, train_writer)
            eval_one_epoch(sess, ops, test_writer)
            
            # Save the variables to disk.
            # if epoch % 10 == 0:
            save_path = saver.save(sess, os.path.join(LOG_DIR, "model.ckpt"))
            log_string("Model saved in file: %s" % save_path)



def train_one_epoch(sess, ops, train_writer):
    """ ops: dict mapping from string to tf ops """
    is_training = True

    total_correct = 0
    total_seen = 0
    loss_sum = 0

    path = "/media/shao/TOSHIBA EXT/data_object_velodyne/Daten_txt_CNN/train"
    filelist = os.listdir(path)
    random.shuffle(filelist)
    num_batches = len(filelist) // BATCH_SIZE
    for f in filelist:
        data_with_label = np.loadtxt(path + "/" + f)
        current_data = data_with_label[:, :-1] 
        current_data = scale_features(current_data, 0) # don't forget the feature scaling!
        # add code
        np.random.shuffle(current_data)
        tree = spatial.KDTree(current_data[:, :-1])
        length = len(current_data)/2
        current_data_ = []
        for i in range(length):
            _, pidx = tree.query(current_data[i, :-1], 10)
            for pi in pidx:
                current_data_.append(current_data[pi])
        current_data_ = np.array(current_data_)
        current_data_ = current_data_[np.newaxis, ...]
        # print('current_data.shape', current_data.shape)   # debug
        current_label = data_with_label[1, -1]
        current_label = current_label.astype(np.int32)
        current_label = current_label[np.newaxis, ...]
        # print('current_label.shape', current_label.shape)  # debug
        # print('current_label', current_label)  # debug
        
        # # Augment batched point clouds by rotation and jittering
        # rotated_data = provider.rotate_point_cloud(current_data[start_idx:end_idx, :, :])
        # jittered_data = provider.jitter_point_cloud(rotated_data)
        feed_dict = {ops['pointclouds_pl']: current_data_,
                        # ops['num_point_pl']: num_point,
                        ops['labels_pl']: current_label,
                        ops['is_training_pl']: is_training,}
        summary, step, _, loss_val, pred_val = sess.run([ops['merged'], ops['step'],
            ops['train_op'], ops['loss'], ops['pred']], feed_dict=feed_dict)
        train_writer.add_summary(summary, step)
        # print('pred_val', pred_val) # debug
        pred_val = np.argmax(pred_val, 1)
        # print('pred_val.shape', pred_val.shape) # debug
        # print('pred_val', pred_val) # debug
        correct = np.sum(pred_val == current_label)
        # print('correct', correct) # debug
        total_correct += correct
        total_seen += BATCH_SIZE
        loss_sum += loss_val
    
    log_string('mean loss: %f' % (loss_sum / float(num_batches)))
    log_string('accuracy: %f' % (total_correct / float(total_seen)))

        
def eval_one_epoch(sess, ops, test_writer):
    """ ops: dict mapping from string to tf ops """
    is_training = False
    total_correct = 0
    total_seen = 0
    loss_sum = 0
    total_seen_class = [0 for _ in range(NUM_CLASSES)]
    total_correct_class = [0 for _ in range(NUM_CLASSES)]  

    path = "/media/shao/TOSHIBA EXT/data_object_velodyne/Daten_txt_CNN/test"
    filelist = os.listdir(path)
    random.shuffle(filelist)
    num_batches = len(filelist) // BATCH_SIZE
    for f in filelist:
        data_with_label = np.loadtxt(path + "/" + f)
        current_data = data_with_label[:, :-1] 
        current_data = scale_features(current_data, 0)  # don't forget the feature scaling!
        # add code
        np.random.shuffle(current_data)
        tree = spatial.KDTree(current_data[:, :-1])
        length = len(current_data)/2
        current_data_ = []
        for i in range(length):
            _, pidx = tree.query(current_data[i, :-1], 10)
            for pi in pidx:
                current_data_.append(current_data[pi])
        current_data_ = np.array(current_data_)
        current_data_ = current_data_[np.newaxis, ...]
        # print('current_data.shape', current_data.shape)
        current_label = data_with_label[1, -1]
        current_label = current_label.astype(np.int32)
        current_label = current_label[np.newaxis, ...]
        # print('current_label.shape', current_label.shape)
        # print('current_label', current_label)          

        feed_dict = {ops['pointclouds_pl']: current_data_,
                        ops['labels_pl']: current_label,
                        ops['is_training_pl']: is_training}
        summary, step, loss_val, pred_val = sess.run([ops['merged'], ops['step'],
            ops['loss'], ops['pred']], feed_dict=feed_dict)
        # print('eval_pred_val', pred_val) # debug
        pred_val = np.argmax(pred_val, 1)
        # print('eval_pred_val', pred_val) # debug
        correct = np.sum(pred_val == current_label)
        total_correct += correct
        total_seen += BATCH_SIZE
        loss_sum += (loss_val*BATCH_SIZE)
        total_seen_class[current_label[0]] += 1
        total_correct_class[current_label[0]] += (pred_val == current_label)
            
    log_string('eval mean loss: %f' % (loss_sum / float(total_seen)))
    log_string('eval accuracy: %f'% (total_correct / float(total_seen)))
    log_string('eval avg class acc: %f' % (np.mean(np.array(total_correct_class)/np.array(total_seen_class,dtype=np.float))))
         


if __name__ == "__main__":
    train()
    LOG_FOUT.close()
