import sys
import os
import time
import pickle
import numpy as np
from numpy.linalg import norm, svd, solve, qr
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.neighbors import NearestNeighbors
import networkx as nx

from scipy.sparse import csr_matrix

# !git clone https://github.com/dx-li/pycvxcluster.git
sys.path.append("./pycvxcluster/src/")
import pycvxcluster.pycvxcluster

from SpLSI.utils import *
from SpLSI.spatialSVD import *
from SpLSI.data_helpers import *
from SpLSI import splsi
import utils.spatial_lda.model
from utils.spatial_lda.featurization import make_merged_difference_matrices


def preprocess_spleen_(
    minx, maxx, miny, maxy, tumor, spleen_coord, spleen_edge, spleen_D, phi, plot_sub, s
):
    # normalize coordinate to (0,1)
    spleen_coord.loc[tumor, ["x", "y"]] = normaliza_coords(spleen_coord.loc[tumor])

    # get weight
    spleen_edge.loc[tumor, ["weight"]] = dist_to_exp_weight(
        spleen_edge.loc[tumor], spleen_coord.loc[tumor], phi
    )

    # edge, coord, X, weights
    edge_df = spleen_edge.loc[tumor]
    coord_df = spleen_coord.loc[tumor]
    nodes = coord_df.index.tolist()
    D = spleen_D.loc[tumor]
    row_sums = D.sum(axis=1)
    X = D.div(row_sums, axis=0)  # normalize
    n = X.shape[0]
    weights = csr_matrix(
        (edge_df["weight"].values, (edge_df["src"].values, edge_df["tgt"].values)),
        shape=(n, n),
    )

    # plot subset nodes (optional)
    if plot_sub:
        plt.scatter(x=coord_df["x"], y=coord_df["y"], s=s)
        plt.plot(
            [minx, minx, maxx, maxx, minx], [miny, maxy, maxy, miny, miny], color="red"
        )

    # subset nodes (optional)
    if maxx * maxy < 1.0 or minx * miny > 0.0:
        print("Subsetting cells...")
        # subset nodes
        samp_coord = coord_df.loc[
            (coord_df["x"] < maxx)
            & (coord_df["x"] > minx)
            & (coord_df["y"] < maxy)
            & (coord_df["y"] > miny)
        ]
        nodes = samp_coord.index.tolist()
        n = len(nodes)
        edge_df = edge_df.loc[
            (edge_df["src"].isin(nodes)) & (edge_df["tgt"].isin(nodes))
        ]
        cell_dict = dict(zip(nodes, range(n)))
        edge_df_ = edge_df.copy()
        edge_df_["src"] = edge_df["src"].map(cell_dict).values
        edge_df_["tgt"] = edge_df["tgt"].map(cell_dict).values
        weights = csr_matrix(
            (
                edge_df_["weight"].values,
                (edge_df_["src"].values, edge_df_["tgt"].values),
            ),
            shape=(n, n),
        )
        X = X.iloc[nodes]
        edge_df = edge_df_
        coord_df = samp_coord

    return X, edge_df, coord_df, weights, n, nodes


def preprocess_spleen(tumor, coord_df, edge_df, D, phi):
    # normalize coordinate to (0,1)
    coord_df.loc[tumor, ["x", "y"]] = normaliza_coords(coord_df.loc[tumor])

    # get weight
    edge_df.loc[tumor, ["weight"]] = dist_to_exp_weight(
        edge_df.loc[tumor], coord_df.loc[tumor], phi
    )

    # edge, coord, X, weights
    edge_df = edge_df.loc[tumor]
    coord_df = coord_df.loc[tumor]
    nodes = coord_df.index.tolist()
    D = D.loc[tumor]
    row_sums = D.sum(axis=1)
    X = D.div(row_sums, axis=0)  # normalize
    n = X.shape[0]
    weights = csr_matrix(
        (edge_df["weight"].values, (edge_df["src"].values, edge_df["tgt"].values)),
        shape=(n, n),
    )

    return X, edge_df, coord_df, weights, n, nodes


def run_spleen(
    minx, maxx, miny, maxy, tumor, coord_df, edge_df, D, K, phi, plot_sub, s
):
    results = []
    # X, edge_df, coord_df, weights, n, nodes = preprocess_spleen(tumor, coord_df, edge_df, D, phi)
    X, edge_df, coord_df, weights, n, nodes = preprocess_spleen_(
        minx, maxx, miny, maxy, tumor, coord_df, edge_df, D, phi, plot_sub, s
    )
    # SPLSI
    start_time = time.time()
    model_splsi = splsi.SpLSI(
        lamb_start=0.0001, step_size=1.17, grid_len=50, step="two-step", verbose=1
    )
    model_splsi.fit(X.values, K, edge_df, weights)
    time_splsi = time.time() - start_time

    # PLSI
    start_time = time.time()
    model_plsi = splsi.SpLSI(
        lamb_start=0.0001, step_size=1.17, grid_len=50, method="nonspatial", verbose=1
    )
    model_plsi.fit(X.values, K, edge_df, weights)
    time_plsi = time.time() - start_time

    # SLDA
    cell_org = [x[1] for x in D.loc[tumor].index]
    cell_dict = dict(zip(range(len(D.loc[tumor])), cell_org))
    samp_coord_ = coord_df.copy()
    samp_coord_.index = coord_df.index.map(cell_dict)
    samp_coord__ = {tumor: samp_coord_}
    difference_matrices = make_merged_difference_matrices(X, samp_coord__, "x", "y")
    print("Running SLDA...")
    start_time = time.time()
    model_slda = utils.spatial_lda.model.train(
        sample_features=X,
        difference_matrices=difference_matrices,
        difference_penalty=0.25,
        n_topics=K,
        n_parallel_processes=2,
        verbosity=1,
        admm_rho=0.1,
        primal_dual_mu=1e5,
    )
    time_slda = time.time() - start_time

    # Metrics
    cell_dict = dict(zip(nodes, range(n)))
    coord_df_ = coord_df.copy()
    coord_df_.index = coord_df.index.map(cell_dict)
    W_splsi = model_splsi.W_hat
    gmoran_splsi, moran_splsi = moran(W_splsi, edge_df)
    gchaos_splsi, chaos_splsi = get_CHAOS(W_splsi, nodes, coord_df_, n, K)
    pas_splsi = get_PAS(W_splsi, edge_df)

    W_plsi = model_plsi.W_hat
    gmoran_plsi, moran_plsi = moran(W_plsi, edge_df)
    gchaos_plsi, chaos_plsi = get_CHAOS(W_plsi, nodes, coord_df_, n, K)
    pas_plsi = get_PAS(W_plsi, edge_df)

    W_slda = model_slda.topic_weights.values
    gmoran_slda, moran_slda = moran(W_slda, edge_df)
    gchaos_slda, chaos_slda = get_CHAOS(W_slda, nodes, coord_df_, n, K)
    pas_slda = get_PAS(W_slda, edge_df)

    # Align A_hat
    A_hat_splsi = model_splsi.A_hat.T
    A_hat_plsi = model_plsi.A_hat.T
    A_hat_slda = model_slda.components_
    row_sums = A_hat_slda.sum(axis=1, keepdims=True)
    A_hat_slda = (A_hat_slda / row_sums).T

    # Plot
    names = ["SPLSI", "PLSI", "SLDA"]
    morans = [gmoran_splsi, gmoran_plsi, gmoran_slda]
    moran_locals = [moran_splsi, moran_plsi, moran_slda]
    chaoss = [gchaos_splsi, gchaos_plsi, gchaos_slda]
    chaos_locals = [chaos_splsi, chaos_plsi, chaos_slda]
    pas = [pas_splsi, pas_plsi, pas_slda]
    times = [time_splsi, time_plsi, time_slda]
    Whats = [W_splsi, W_plsi, W_slda]
    Ahats = [A_hat_splsi, A_hat_plsi, A_hat_slda]

    # fig, axes = plt.subplots(1,3, figsize=(18,6))
    # for j, ax in enumerate(axes):
    #    w = np.argmax(Whats[j], axis=1)
    #    samp_coord_ = coord_df.copy()
    #    samp_coord_['tpc'] = w
    #    sns.scatterplot(x='x',y='y',hue='tpc', data=samp_coord_, palette='viridis', ax=ax, s=20)
    #    name = names[j]
    #    ax.set_title(f'{name} (chaos:{np.round(chaoss[j],8)}, moran:{np.round(morans[j],3)}, time:{np.round(times[j],2)})')
    # plt.tight_layout()
    # plt.show()

    results.append(
        {
            "Whats": Whats,
            "Ahats": Ahats,
            "chaoss": chaoss,
            "chaos_locals": chaos_locals,
            "morans": morans,
            "moran_locals": moran_locals,
            "pas": pas,
            "times": times,
            "coord_df": coord_df,
            "edge_df": edge_df,
        }
    )

    return results


if __name__ == "__main__":
    dataset_root = "data/spleen"
    model_root = os.path.join(dataset_root, "model")
    fig_root = os.path.join(dataset_root, "fig")
    path_to_D = os.path.join(dataset_root, "merged_D.pkl")
    path_to_edge = os.path.join(dataset_root, "merged_data.pkl")
    path_to_coord = os.path.join(dataset_root, "merged_coord.pkl")
    os.makedirs(model_root, exist_ok=True)
    os.makedirs(fig_root, exist_ok=True)

    spleen_D = pd.read_pickle(path_to_D)
    spleen_edge = pd.read_pickle(path_to_edge)
    spleen_coord = pd.read_pickle(path_to_coord)

    # tumors = ['BALBc-1', 'BALBc-2', 'BALBc-3']
    tumors = ["BALBc-1"]
    spleen_coord.columns = ["x", "y"]
    spleen_edge.columns = ["src", "tgt", "distance"]

    for tumor in tumors:
        path_to_model = os.path.join(model_root, "%s.model.pkl" % tumor)
        ntopics_list = [3, 5, 7]
        spatial_models = {}
        for ntopic in ntopics_list:
            res = run_spleen(
                minx=0.1,
                maxx=0.4,
                miny=0.3,
                maxy=0.6,
                tumor="BALBc-1",
                coord_df=spleen_coord,
                edge_df=spleen_edge,
                D=spleen_D,
                K=ntopic,
                phi=0.1,
                plot_sub=False,
                s=0.5,
            )
            spatial_models[ntopic] = res
        aligned_models = plot_topic(
            spatial_models, ntopics_list, fig_root, "BALBc-1", 15
        )
        with open(path_to_model, "wb") as f:
            pickle.dump(aligned_models, f)
