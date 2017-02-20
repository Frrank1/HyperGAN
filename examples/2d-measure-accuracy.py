import argparse
import os
import uuid
import tensorflow as tf
import hypergan as hg
import hyperchamber as hc
import matplotlib.pyplot as plt
from hypergan.loaders import *
from hypergan.util.hc_tf import *

def parse_args():
    parser = argparse.ArgumentParser(description='Train a 2d test!', add_help=True)
    parser.add_argument('--batch_size', '-b', type=int, default=32, help='Examples to include in each batch.  If using batch norm, this needs to be preserved when in server mode')
    parser.add_argument('--device', '-d', type=str, default='/gpu:0', help='In the form "/gpu:0", "/cpu:0", etc.  Always use a GPU (or TPU) to train')
    parser.add_argument('--format', '-f', type=str, default='png', help='jpg or png')
    parser.add_argument('--config', '-c', type=str, default='2d-test', help='config name')
    parser.add_argument('--distribution', '-t', type=str, default='circle', help='what distribution to test, options are circle, modes')
    return parser.parse_args()

def no_regularizer(amt):
    return None
 
def custom_discriminator_config(regularizer=no_regularizer, regularizer_lambda=0.0001):
    return { 
            'create': custom_discriminator, 
            'regularizer': regularizer,
            'regularizer_lambda': regularizer_lambda
    }

def custom_generator_config(regularizer=no_regularizer, regularizer_lambda=0.0001):
    return { 
            'create': custom_generator,
            'regularizer': regularizer,
            'regularizer_lambda': regularizer_lambda
    }

def custom_discriminator(gan, config, x, g, xs, gs, prefix='d_'):
    net = tf.concat(axis=0, values=[x,g])
    net = linear(net, 128, scope=prefix+'lin1', regularizer=config.regularizer(config.regularizer_lambda))
    net = tf.tanh(net)
    net = linear(net, 128, scope=prefix+'lin2', regularizer=config.regularizer(config.regularizer_lambda))
    return net

def custom_generator(config, gan, net):
    net = linear(net, 128, scope="g_lin_proj", regularizer=config.regularizer(config.regularizer_lambda))
    net = batch_norm_1('g_bn_1')(net)
    net = tf.tanh(net)
    net = linear(net, 2, scope="g_lin_proj2", regularizer=config.regularizer(config.regularizer_lambda))
    return [net]

def batch_accuracy(a, b):
    "Each point of a is measured against the closest point on b.  Distance differences are added together."
    tiled_a = a
    tiled_a = tf.reshape(tiled_a, [int(tiled_a.get_shape()[0]), 1, int(tiled_a.get_shape()[1])])

    tiled_a = tf.tile(tiled_a, [1, int(tiled_a.get_shape()[0]), 1])

    tiled_b = b
    tiled_b = tf.reshape(tiled_b, [1, int(tiled_b.get_shape()[0]), int(tiled_b.get_shape()[1])])
    tiled_b = tf.tile(tiled_b, [int(tiled_b.get_shape()[0]), 1, 1])

    difference = tf.abs(tiled_a-tiled_b)
    difference = tf.reduce_min(difference, axis=1)
    difference = tf.reduce_sum(difference, axis=1)
    return tf.reduce_sum(difference, axis=0) 


args = parse_args()

def train():
    selector = hg.config.selector(args)
    config_name="2d-measure-accuracy-"+str(uuid.uuid4())

    config = selector.random_config()
    config_filename = os.path.expanduser('~/.hypergan/configs/'+config_name+'.json')

    trainers = []
    trainers.append(hg.trainers.adam_trainer.config())

    rms_opts = {
        'g_momentum': [0,1e-6],
        'd_momentum': [0,1e-6],
        'd_decay': [0.9,0.99,0.999,0.995],
        'g_decay': [0.9,0.99,0.999,0.995],
        'clipped_gradients': [False, 1e-4],
        'clipped_d_weights': [False, 1e-2],
        'd_learn_rate': [1e-4, 1e-5, 2e-4, 1e-3],
        'g_learn_rate': [1e-4, 1e-5, 2e-4, 1e-3]
    }
    trainers.append(hg.trainers.rmsprop_trainer.config(**rms_opts))

    encoders = []
    encoder_opts = {
            'z': 2,
            'modes': 2,
            'projections': [[hg.encoders.linear_encoder.modal]]
            }
    encoders.append([hg.encoders.linear_encoder.config(**encoder_opts)])
    custom_config = {
        'model': args.config,
        'batch_size': args.batch_size,
        'trainer': trainers,
        'generator': custom_generator_config(),
        'discriminators': [[custom_discriminator_config()]],
        'encoders': encoders
    }

    custom_config_selector = hc.Selector()
    for key,value in custom_config.items():
        custom_config_selector.set(key, value)
        print("Set ", key, value)
    
    custom_config_selection = custom_config_selector.random_config()

    for key,value in custom_config_selection.items():
        config[key]=value

    
    config['dtype']=tf.float32
    config = hg.config.lookup_functions(config)

    def circle(x):
        spherenet = tf.square(x)
        spherenet = tf.reduce_sum(spherenet, 1)
        lam = tf.sqrt(spherenet)
        return x/tf.reshape(lam,[int(lam.get_shape()[0]), 1])

    def modes(x):
        return tf.round(x*2)/2.0

    if args.distribution == 'circle':
        x = tf.random_normal([args.batch_size, 2])
        x = circle(x)
    elif args.distribution == 'modes':
        x = tf.random_uniform([args.batch_size, 2], -1, 1)
        x = modes(x)

    initial_graph = {
            'x':x,
            'num_labels':1,
            }

    selector.save(config_filename, config)

    with tf.device(args.device):
        gan = hg.GAN(config, initial_graph)

        accuracy_x_to_g=batch_accuracy(gan.graph.x, gan.graph.g[0])
        accuracy_g_to_x=batch_accuracy(gan.graph.g[0], gan.graph.x)
        x_0 = gan.sess.run(gan.graph.x)
        z_0 = gan.sess.run(gan.graph.z[0])

        gan.initialize_graph()

        ax_sum = 0
        ag_sum = 0
        dlog = 0

        tf.train.start_queue_runners(sess=gan.sess)
        for i in range(100000):
            d_loss, g_loss = gan.train()

            if(i > 99500):
                ax, ag, dl = gan.sess.run([accuracy_x_to_g, accuracy_g_to_x, gan.graph.d_log], {gan.graph.x: x_0, gan.graph.z[0]: z_0})
                ax_sum += ax
                ag_sum += ag
                dlog = dl

        with open("results.csv", "a") as myfile:
            myfile.write(config_name+","+str(ax_sum)+","+str(ag_sum)+","+ str(ax_sum+ag_sum)+","+str(dlog)+"\n")
        tf.reset_default_graph()
        gan.sess.close()

while(True):
    train()
