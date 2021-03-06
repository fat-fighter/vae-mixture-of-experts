import os
import priors
import numpy as np
import tensorflow as tf

from tqdm import tqdm
from sklearn.mixture import GaussianMixture

from includes.network import FeedForwardNetwork, DeepNetwork
from includes.utils import get_clustering_accuracy
from includes.layers import Convolution, MaxPooling


class VAE:
    def __init__(self, name, input_type, input_dim, latent_dim, activation=None, initializer=None):
        self.name = name

        self.input_dim = input_dim
        self.latent_dim = latent_dim

        self.input_type = input_type

        self.activation = activation
        self.initializer = initializer

        self.path = ""

        self.kl_ratio = tf.placeholder_with_default(
            1.0, shape=None, name="kl_ratio"
        )

        self.is_training = tf.placeholder_with_default(
            True, shape=None, name="is_training"
        )

        self.X = None
        self.decoded_X = None
        self.train_step = None
        self.latent_variables = dict()

    def build_graph(self, encoder_layer_sizes, decoder_layer_sizes):
        raise NotImplementedError

    def sample_reparametrization_variables(self, n, variables=None):
        samples = dict()
        if variables is None:
            for lv, eps, _ in self.latent_variables.values():
                if eps is not None:
                    samples[eps] = lv.sample_reparametrization_variable(n)
        else:
            for var in variables:
                lv, eps, _ = self.latent_variables[var]
                if eps is not None:
                    samples[eps] = lv.sample_reparametrization_variable(n)

        return samples

    def sample_generative_feed(self, n, **kwargs):
        samples = dict()
        for name, (lv, _, _) in self.latent_variables.items():
            kwargs_ = dict() if name not in kwargs else kwargs[name]
            samples[name] = lv.sample_generative_feed(n, **kwargs_)

        return samples

    def define_latent_loss(self):
        self.latent_loss = tf.add_n(
            [lv.kl_from_prior(params)
             for lv, _, params in self.latent_variables.values()]
        )

    def define_recon_loss(self):
        if self.input_type == "binary":
            self.recon_loss = tf.reduce_mean(tf.reduce_sum(
                tf.nn.sigmoid_cross_entropy_with_logits(
                    labels=self.X,
                    logits=self.decoded_X
                ), axis=1
            ))
        elif self.input_type == "real":
            self.recon_loss = 0.5 * tf.reduce_mean(tf.reduce_sum(
                tf.square(self.X - self.decoded_X), axis=1
            ))
        else:
            raise NotImplementedError

    def define_train_loss(self):
        self.define_latent_loss()
        self.define_recon_loss()

        self.loss = tf.reduce_mean(
            self.recon_loss + self.kl_ratio * self.latent_loss
        )

    def define_train_step(self, init_lr, decay_steps, decay_rate=0.9):
        learning_rate = tf.train.exponential_decay(
            learning_rate=init_lr,
            global_step=0,
            decay_steps=decay_steps,
            decay_rate=decay_rate
        )
        optimizer = tf.train.AdamOptimizer(
            learning_rate=learning_rate
        )

        self.define_train_loss()

        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            self.train_step = optimizer.minimize(self.loss)

    def train_op(self, session, data, kl_ratio=1.0):
        assert(self.train_step is not None)

        loss = 0.0
        for batch in data.get_batches():
            feed = {
                self.X: batch,
                self.is_training: True,
                self.kl_ratio: kl_ratio
            }
            feed.update(
                self.sample_reparametrization_variables(len(batch))
            )

            batch_loss, _ = session.run(
                [self.loss, self.train_step],
                feed_dict=feed
            )
            loss += batch_loss / data.epoch_len
        
        return loss

    def debug(self, session, data):
        import pdb

        for batch in data.get_batches():
            feed = {
                self.X: batch
            }
            feed.update(
                self.sample_reparametrization_variables(len(batch))
            )

            pdb.set_trace()

            break


class DeepMixtureVAE(VAE):
    def __init__(self, name, input_type, input_dim, latent_dim, n_classes, activation=None, initializer=None, cnn=False):
        VAE.__init__(self, name, input_type, input_dim, latent_dim,
                     activation=activation, initializer=initializer)

        self.n_classes = n_classes
        self.cnn = True#cnn

    def build_graph(self):
        with tf.variable_scope(self.name) as _:
            self.X = tf.placeholder(
                tf.float32, shape=(None, self.input_dim), name="X"
            )
            self.epsilon = tf.placeholder(
                tf.float32, shape=(None, self.latent_dim), name="epsilon_Z"
            )
            self.cluster = tf.placeholder(
                tf.float32, shape=(None, 1, self.n_classes), name="epsilon_C"
            )

            self.latent_variables = dict()

            # DMVAE  - shared    1   nbn   pretrained   ->   Working???? (gpu - dmvae, 94.86)

            with tf.variable_scope("encoder_network"):

                X_flat = tf.reshape(self.X, (-1, 28, 28, 1))

                if self.cnn:
                    encoder_network = DeepNetwork(
                        "layers",
                        [
                            ("cn", {
                                "n_kernels": 32, "prev_n_kernels": 1, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 32, "prev_n_kernels": 32, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("cn", {
                                "n_kernels": 64, "prev_n_kernels": 32, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 64, "prev_n_kernels": 64, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("cn", {
                                "n_kernels": 128, "prev_n_kernels": 64, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 128, "prev_n_kernels": 128, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("fc", {"input_dim": 2048, "output_dim": 500})
                        ],
                        # Following for fast testing
                        # [
                        #     ("cn", {
                        #         "n_kernels": 32, "prev_n_kernels": 1, "kernel": (3, 3)
                        #     }),
                        #     ("mp", {"k": 5}),
                        #     ("fc", {"input_dim": 1152, "output_dim": 128})
                        # ],
                        activation=self.activation,
                        initializer=self.initializer
                    )
                    hidden = encoder_network(X_flat)

                else:

                    hidden = self.X
                    hidden = tf.layers.dense(
                        hidden, 500, activation=self.activation, kernel_initializer=self.initializer()
                    )
                    hidden = tf.layers.dense(
                        hidden, 500, activation=self.activation, kernel_initializer=self.initializer()
                    )


                with tf.variable_scope("z"):
                    hidden_z = tf.layers.dense(
                        hidden, 2000, activation=self.activation, kernel_initializer=self.initializer()
                    )

                    self.mean = tf.layers.dense(
                        hidden_z, self.latent_dim, activation=None, kernel_initializer=self.initializer()
                    )
                    self.log_var = tf.layers.dense(
                        hidden_z, self.latent_dim, activation=None, kernel_initializer=self.initializer()
                    )

                with tf.variable_scope("c"):
                    hidden_c = tf.layers.dense(
                        hidden, 2000, activation=self.activation, kernel_initializer=self.initializer()
                    )

                    self.logits = tf.layers.dense(
                        hidden_c, self.n_classes, activation=None, kernel_initializer=self.initializer()
                    )
                    self.cluster_probs = tf.nn.softmax(self.logits)

                self.reconstructed_Y_soft = tf.layers.dense(
                    hidden, 10, activation=tf.nn.softmax, kernel_initializer=self.initializer()
                )


            self.latent_variables.update({
                "C": (
                    priors.DiscreteFactorial(
                        "cluster", 1, self.n_classes
                    ), self.cluster,
                    {"logits": self.logits}
                ),
                "Z": (
                    priors.NormalMixtureFactorial(
                        "representation", self.latent_dim, self.n_classes
                    ), self.epsilon,
                    {
                        "mean": self.mean,
                        "log_var": self.log_var,
                        "weights": self.cluster_probs,
                        "cluster_sample": False
                    }
                )
            })

            lv, eps, params = self.latent_variables["Z"]
            self.Z = lv.inverse_reparametrize(eps, params)

            with tf.variable_scope("decoder_network"):
                decoder_network = DeepNetwork(
                    "layers",
                    [
                        ("fc", {"input_dim": self.latent_dim, "output_dim": 2000}),
                        ("fc", {"input_dim": 2000, "output_dim": 500}),
                        ("fc", {"input_dim": 500, "output_dim": 500}),
                    ],
                    activation=self.activation, initializer=self.initializer
                )
                hidden = decoder_network(self.Z)

                self.decoded_X = tf.layers.dense(
                    hidden, self.input_dim, activation=None, kernel_initializer=self.initializer()
                )

            if self.input_type == "binary":
                self.reconstructed_X = tf.nn.sigmoid(self.decoded_X)
            elif self.input_type == "real":
                self.reconstructed_X = self.decoded_X
            else:
                raise NotImplementedError

        return self

    def define_pretrain_step(self, vae_lr, prior_lr):
        self.define_train_loss()

        self.vae_loss = self.recon_loss
        self.vae_train_step = tf.train.AdamOptimizer(
            learning_rate=vae_lr
        ).minimize(self.recon_loss)

        prior_var_list = tf.get_collection(
            tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name + "/encoder_network/c"
        )
        # + tf.get_collection(
        #     tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name + "/representation"
        # )

        self.prior_train_step = tf.train.AdamOptimizer(
            learning_rate=prior_lr
        ).minimize(self.latent_loss, var_list=prior_var_list)

    def pretrain_vae(self, session, data, n_epochs):
        var_list = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
        saver = tf.train.Saver(var_list)
        ckpt_path = self.path + "/vae/parameters.ckpt"

        try:
            saver.restore(session, ckpt_path)
        except:
            print("Could not load trained ae parameters")

        min_loss = float("inf")
        with tqdm(range(n_epochs)) as bar:
            for _ in bar:
                loss = 0
                for batch in data.get_batches():
                    feed = {
                        self.X: batch,
                        self.epsilon: np.zeros(
                            (len(batch), self.latent_dim)
                        ),
                        self.is_training: True
                    }

                    batch_loss, _ = session.run(
                        [self.recon_loss, self.vae_train_step], feed_dict=feed
                    )
                    loss += batch_loss / data.epoch_len

                bar.set_postfix({"loss": "%.4f" % loss})

                if loss <= min_loss:
                    min_loss = loss
                    saver.save(session, ckpt_path)

    def pretrain_prior(self, session, data, n_epochs):
        var_list = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
        saver = tf.train.Saver(var_list)
        ckpt_path = self.path + "/prior/parameters.ckpt"

        try:
            saver.restore(session, ckpt_path)
        except:
            print("Could not load trained prior parameters")

            if n_epochs > 0:
                feed = {
                    self.X: data.data
                }
                Z = session.run(self.mean, feed_dict=feed)

                gmm_model = GaussianMixture(
                    n_components=self.n_classes,
                    covariance_type="diag",
                    max_iter=n_epochs,
                    n_init=20,
                    weights_init=np.ones(self.n_classes) / self.n_classes,
                )
                gmm_model.fit(Z)

                lv = self.latent_variables["Z"][0]

                init_prior_means = tf.assign(lv.means, gmm_model.means_)
                init_prior_vars = tf.assign(
                    lv.log_vars, np.log(gmm_model.covariances_ + 1e-20)
                )

                session.run([init_prior_means, init_prior_vars])
                saver.save(session, ckpt_path)

        min_loss = float("inf")
        with tqdm(range(n_epochs)) as bar:
            for _ in bar:
                loss = 0
                for batch in data.get_batches():
                    feed = {
                        self.X: batch,
                        self.epsilon: np.zeros(
                            (len(batch), self.latent_dim)
                        ),
                        self.is_training: True
                    }

                    batch_loss, _ = session.run(
                        [self.latent_loss, self.prior_train_step], feed_dict=feed
                    )
                    loss += batch_loss / data.epoch_len

                bar.set_postfix({"loss": "%.4f" % loss})

                if loss <= min_loss:
                    min_loss = loss
                    saver.save(session, ckpt_path)

    def pretrain(self, session, data, n_epochs_vae, n_epochs_gmm):
        assert(
            self.vae_train_step is not None and
            self.prior_train_step is not None
        )

        self.pretrain_vae(session, data, n_epochs_vae)
        self.pretrain_prior(session, data, n_epochs_gmm)

    def get_accuracy(self, session, data):
        logits = []
        for batch in data.get_batches():
            logits.append(session.run(self.logits, feed_dict={self.X: batch}))

        logits = np.concatenate(logits, axis=0)

        return get_clustering_accuracy(logits, data.classes)


class VaDE(VAE):
    def __init__(self, name, input_type, input_dim, latent_dim, n_classes, activation=None, initializer=None, cnn=False):
        VAE.__init__(self, name, input_type, input_dim, latent_dim,
                     activation=activation, initializer=initializer)

        self.n_classes = n_classes
        self.cnn = cnn

    def build_graph(self):
        with tf.variable_scope(self.name) as _:
            self.X = tf.placeholder(
                tf.float32, shape=(None, self.input_dim), name="X"
            )
            self.epsilon = tf.placeholder(
                tf.float32, shape=(None, self.latent_dim), name="epsilon"
            )

            self.latent_variables = dict()

            X_flat = tf.reshape(self.X, (-1, 28, 28, 1))
            with tf.variable_scope("encoder_network"):

                if self.cnn:
                    encoder_network = DeepNetwork(
                        "layers",
                        [
                            ("cn", {
                                "n_kernels": 32, "prev_n_kernels": 1, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 32, "prev_n_kernels": 32, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("cn", {
                                "n_kernels": 64, "prev_n_kernels": 32, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 64, "prev_n_kernels": 64, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("cn", {
                                "n_kernels": 128, "prev_n_kernels": 64, "kernel": (3, 3)
                            }),
                            ("cn", {
                                "n_kernels": 128, "prev_n_kernels": 128, "kernel": (3, 3)
                            }),
                            ("mp", {"k": 2}),
                            ("fc", {"input_dim": 2048, "output_dim": 128})
                        ],    
                        activation=self.activation,
                        initializer=self.initializer
                    )
                    hidden = encoder_network(X_flat)
                else:
                    
                    encoder_network = DeepNetwork(
                    "layers",
                    [
                        ("fc", {"input_dim": self.input_dim, "output_dim": 2000}),
                        ("fc", {"input_dim": 2000, "output_dim": 500}),
                        ("fc", {"input_dim": 500, "output_dim": 500})
                    ],
                    activation=self.activation, initializer=self.initializer
                    )
                    hidden = encoder_network(self.X)


                with tf.variable_scope("z"):
                    self.mean = tf.layers.dense(
                        hidden, self.latent_dim, activation=None, kernel_initializer=self.initializer()
                    )
                    self.log_var = tf.layers.dense(
                        hidden, self.latent_dim, activation=None, kernel_initializer=self.initializer()
                    )

            self.latent_variables.update({
                "Z": (
                    priors.NormalMixtureFactorial(
                        "representation", self.latent_dim, self.n_classes
                    ), self.epsilon,
                    {
                        "mean": self.mean,
                        "log_var": self.log_var,
                        "cluster_sample": False
                    }
                )
            })

            lv, eps, params = self.latent_variables["Z"]
            self.Z = lv.inverse_reparametrize(eps, params)

            self.cluster_probs = lv.get_cluster_probs(self.Z)
            params["weights"] = self.cluster_probs

            self.latent_variables.update({
                "C": (
                    priors.DiscreteFactorial(
                        "cluster", 1, self.n_classes
                    ), None,
                    {"probs": self.cluster_probs}
                )
            })

            with tf.variable_scope("decoder_network"):
                decoder_network = DeepNetwork(
                    "layers",
                    [
                        ("fc", {"input_dim": self.latent_dim, "output_dim": 500}),
                        ("fc", {"input_dim": 500, "output_dim": 500}),
                        ("fc", {"input_dim": 500, "output_dim": 2000})
                    ],
                    activation=self.activation, initializer=self.initializer
                )
                hidden = decoder_network(self.Z)

                self.decoded_X = tf.layers.dense(
                    hidden, self.input_dim, activation=None, kernel_initializer=self.initializer()
                )


            if self.input_type == "binary":
                self.reconstructed_X = tf.nn.sigmoid(self.decoded_X)
            elif self.input_type == "real":
                self.reconstructed_X = self.decoded_X
            else:
                raise NotImplementedError

        return self

    # def define_latent_loss(self):
    #     self.latent_loss = tf.add_n(
    #         [lv.kl_from_prior(params)
    #          for lv, _, params in self.latent_variables.values()]
    #     )
    #     self.latent_loss += tf.reduce_mean(tf.reduce_sum(
    #         self.cluster_probs * tf.log(self.cluster_probs + 1e-20),
    #         axis=-1
    #     ))

    def define_pretrain_step(self, vae_lr, _prior_lr=None):
        self.define_train_loss()

        self.vae_loss = self.recon_loss
        self.vae_train_step = tf.train.AdamOptimizer(
            learning_rate=vae_lr
        ).minimize(self.recon_loss)

    def pretrain_vae(self, session, data, n_epochs):
        saver = tf.train.Saver()
        ckpt_path = self.path + "/vae/parameters.ckpt"

        try:
            saver.restore(session, ckpt_path)
        except:
            print("Could not load pretrained vae model")

        min_loss = float("inf")
        with tqdm(range(n_epochs)) as bar:
            for _ in bar:
                loss = 0
                for batch in data.get_batches():
                    feed = {
                        self.X: batch,
                        self.epsilon: np.zeros(
                            (len(batch), self.latent_dim)
                        )
                    }

                    batch_loss, _ = session.run(
                        [self.recon_loss, self.vae_train_step], feed_dict=feed
                    )
                    loss += batch_loss / data.epoch_len

                bar.set_postfix({"loss": "%.4f" % loss})

                if loss <= min_loss:
                    min_loss = loss
                    saver.save(session, ckpt_path)

    def pretrain_prior(self, session, data, n_epochs):
        saver = tf.train.Saver()
        ckpt_path = self.path + "/prior/parameters.ckpt"

        try:
            saver.restore(session, ckpt_path)
        except:
            print("Could not load pretrained prior parameters")

            if n_epochs > 0:
                feed = {
                    self.X: data.data
                }
                Z = session.run(self.mean, feed_dict=feed)

                gmm_model = GaussianMixture(
                    n_components=self.n_classes,
                    covariance_type="diag",
                    max_iter=n_epochs,
                    n_init=5,
                    weights_init=np.ones(self.n_classes) / self.n_classes,
                )
                gmm_model.fit(Z)

                lv = self.latent_variables["Z"][0]

                init_prior_means = tf.assign(lv.means, gmm_model.means_)
                init_prior_vars = tf.assign(
                    lv.log_vars, np.log(gmm_model.covariances_ + 1e-20)
                )

                session.run([init_prior_means, init_prior_vars])
                saver.save(session, ckpt_path)

    def pretrain(self, session, data, n_epochs_vae, n_epochs_prior):
        assert(self.vae_train_step is not None)

        self.pretrain_vae(session, data, n_epochs_vae)
        self.pretrain_prior(session, data, n_epochs_prior)

    def get_accuracy(self, session, data, k=10):
        weights = []
        for _ in range(k):
            feed = {self.X: data.data}
            feed.update(
                self.sample_reparametrization_variables(
                    len(data.data), variables=["Z"]
                )
            )
            weights.append(session.run(
                self.cluster_probs, feed_dict=feed
            ))

        weights = np.array(weights)
        weights = np.mean(weights, axis=0)

        return get_clustering_accuracy(weights, data.classes)
