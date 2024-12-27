import streamlit as st
import pandas as pd
import numpy as np
import random
import networkx as nx
from pyvis.network import Network
import tempfile

#############################
# 1. STREAMLIT INTERFACE   #
#############################

def main():
    st.title("Synthetic Social Graph Generator")

    st.markdown("""
    **Instructions**:
    1. Upload an Excel file with columns:
       - **Name**  
       - **Handle**  (unique identifier)  
       - **Faction**  
       - **Tags**  (e.g. "#hub", "#hub_uk", "#uk", etc.)
       - **TwHandle** (optional)  
       - **TwFollowers** (desired # of followers)  
       - **TwFollowing** (desired # of followings)  

    2. Set the probabilities below:
       - **Country-Specific Hub Probability**: if “#hub_xxx” in V's tags and “#xxx” in U's tags  
       - **Global Hub Probability**: if V is tagged “#hub” (international hub)  
       - **Intra-Faction Probability**: if neither is a hub, but U and V share the same faction  
       - **Inter-Faction Probability**: if neither is a hub and U and V are in different factions  

    3. **Bandwagon Effect** (Only Based on `TwFollowers`):
       If V has a high TwFollowers (compared to the dataset max), it is more likely to be followed.

    4. **Big-Follows-Small Controls**:
       - **Ratio Threshold**: If U's TwFollowers / V's TwFollowers exceeds this, U won't follow V.
       - **Min Follower Cutoff**: If V's TwFollowers is below this cutoff, big users won't follow them at all.

    5. The app will create a synthetic "who-follows-whom" graph, ensuring each user follows at least 2 others and is followed by at least 2 others.

    6. In the **Network Diagram**, each node is labeled with the person's Name, and sized by how many followers they have.
    """)

    # Sliders for adjusting probabilities
    hub_country_probability = st.slider(
        "Probability if target has #hub_country (e.g. #hub_uk) and user has #country (e.g. #uk)",
        0.0, 1.0, 0.6, 0.05
    )
    hub_global_probability = st.slider(
        "Probability if target is a global hub (#hub)",
        0.0, 1.0, 0.5, 0.05
    )
    p_intra_faction = st.slider(
        "Probability if same Faction (and not a hub)",
        0.0, 1.0, 0.3, 0.05
    )
    p_inter_faction = st.slider(
        "Probability if different Faction (and not a hub)",
        0.0, 1.0, 0.1, 0.05
    )
    bandwagon_scale = st.slider(
        "Bandwagon Effect Scale (0 = none, higher = stronger effect)",
        0.0, 2.0, 0.5, 0.1
    )

    # New controls for Big-Follows-Small
    big_follow_threshold = st.slider(
        "Big-Follows-Small Ratio Threshold (if U's TwFollowers / V's TwFollowers > this, no follow)",
        1.0, 10.0, 3.0, 0.5
    )
    min_follow_cutoff = st.slider(
        "Minimum TwFollowers cutoff (if V has fewer than this, big user won't follow)",
        0, 50000, 1000, 100
    )

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)

            # Verify required columns
            required_cols = ["Name", "Handle", "Faction", "Tags", 
                             "TwHandle", "TwFollowers", "TwFollowing"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                st.error(f"Error: missing columns {missing_cols} in the uploaded file.")
                return

            st.success("File uploaded successfully! Generating Social Graph...")

            # Generate the graph
            edges, handle_to_name = generate_social_graph(
                df,
                hub_country_probability,
                hub_global_probability,
                p_intra_faction,
                p_inter_faction,
                bandwagon_scale,
                big_follow_threshold,
                min_follow_cutoff
            )

            # Display Edges as a data frame
            st.write("### Final Edges (Follower -> Followed)")
            edges_df = pd.DataFrame(edges, columns=["Follower", "Followed"])
            st.dataframe(edges_df)

            # Display a network diagram (label with Name, not Handle)
            st.write("### Network Diagram (Nodes labeled by Name)")
            display_network_graph(edges, handle_to_name)

        except Exception as e:
            st.error(f"An error occurred: {e}")


#############################
# 2. GRAPH GENERATION LOGIC #
#############################

def generate_social_graph(
    df, 
    hub_country_probability=0.6,
    hub_global_probability=0.5,
    p_intra_faction=0.3, 
    p_inter_faction=0.1,
    bandwagon_scale=0.5,
    big_follow_threshold=3.0,
    min_follow_cutoff=1000
):
    """
    Generate a synthetic social graph, returning:
      edges: list of (follower_handle, followed_handle)
      handle_to_name: map handle -> Name

    Steps:
      1) Faction/hub base probability.
      2) Bandwagon effect (V's TwFollowers vs. dataset max).
      3) "Big-Follows-Small" logic:
         - If U's TwFollowers / V's TwFollowers > big_follow_threshold => p=0.
         - If V["TwFollowers"] < min_follow_cutoff => p=0 if U is big.
      4) If V is "full", reduce p.
      5) Ensure everyone has min=2 followers/following.
    """

    personas = df.to_dict("records")
    handle_to_name = {row["Handle"]: row["Name"] for row in personas}

    # Initialize dynamic counters
    for person in personas:
        person["F_desired"]  = int(person["TwFollowers"])
        person["f_desired"]  = int(person["TwFollowing"])
        person["F_current"]  = 0
        person["f_current"]  = 0
        person["tag_list"]   = str(person["Tags"]).lower().split()

    # Find max TwFollowers for bandwagon
    max_desired_followers = max([p["TwFollowers"] for p in personas if p["TwFollowers"] > 0] or [1])

    random.shuffle(personas)
    edges = []

    for U in personas:
        possible_targets = [p for p in personas if p["Handle"] != U["Handle"]]
        random.shuffle(possible_targets)

        while U["f_current"] < U["f_desired"] and possible_targets:
            V = possible_targets.pop()

            # 1) Base probability
            p = base_probability(
                U, V,
                hub_country_probability,
                hub_global_probability,
                p_intra_faction,
                p_inter_faction
            )

            # 2) Bandwagon effect
            bandwagon_ratio = V["TwFollowers"] / max_desired_followers
            bandwagon_factor = 1 + bandwagon_scale * bandwagon_ratio
            p *= bandwagon_factor

            # 3) Big-Follows-Small logic
            #    (a) ratio check
            ratio_uf_to_vf = U["TwFollowers"] / max(1, V["TwFollowers"]) if V["TwFollowers"]>0 else 99999
            #    (b) cutoff check
            # "If V has fewer than min_follow_cutoff, big user won't follow"
            # We define "big user" as someone with TwFollowers >= the min_follow_cutoff * big_follow_threshold?
            # Or simply if U's TwFollowers is large enough to exceed the ratio threshold?
            # We'll interpret "big user" as ratio_uf_to_vf > 1.0 (meaning U is bigger than V).
            # But you might refine that definition if needed.
            
            is_U_big = (ratio_uf_to_vf > 1.0)  # means U has more TwFollowers than V
            # If ratio is above threshold OR V < cutoff => p=0 if U is indeed "big" compared to V
            if ratio_uf_to_vf > big_follow_threshold or (V["TwFollowers"] < min_follow_cutoff and is_U_big):
                p = 0.0

            # 4) If V is "full"
            if V["F_current"] >= V["F_desired"]:
                p *= 0.2

            # Decide
            r = random.random()
            if r < p:
                edges.append((U["Handle"], V["Handle"]))
                U["f_current"] += 1
                V["F_current"] += 1

    # Final fix => ensure min=2
    edges = ensure_minimum_two(personas, edges)

    return edges, handle_to_name


def base_probability(U, V, 
                     hub_country_probability,
                     hub_global_probability, 
                     p_intra_faction,
                     p_inter_faction):
    """
    Determine the base probability that U follows V.
    """
    hub_country_tag = find_country_hub_tag(V["tag_list"])
    if hub_country_tag is not None:
        if f"#{hub_country_tag}" in U["tag_list"]:
            return hub_country_probability

    if "#hub" in V["tag_list"]:
        return hub_global_probability

    if U["Faction"] == V["Faction"]:
        return p_intra_faction

    return p_inter_faction


def find_country_hub_tag(tag_list):
    """
    Look for '#hub_xxx' in the tags. Return 'xxx' if found, else None.
    """
    for tag in tag_list:
        if tag.startswith("#hub_") and len(tag) > 5:
            return tag.split("_", 1)[1]
    return None


#############################
# 3. FINAL FIX LOGIC        #
#############################

def ensure_minimum_two(personas, edges):
    """
    Ensure each persona has at least 2 followers & 2 following.
    """
    out_edges = {}
    in_edges  = {}
    for p in personas:
        out_edges[p["Handle"]] = set()
        in_edges[p["Handle"]]  = set()

    for (u, v) in edges:
        out_edges[u].add(v)
        in_edges[v].add(u)

    max_tries = 1000
    tries = 0

    while tries < max_tries:
        tries += 1
        changed = False

        for p in personas:
            me  = p["Handle"]
            f_count = len(out_edges[me])  
            F_count = len(in_edges[me])   

            # Need >= 2 following
            if f_count < 2:
                potential_follow_backs = [
                    follower for follower in in_edges[me]
                    if me not in out_edges[follower]
                ]
                if potential_follow_backs:
                    target = random.choice(potential_follow_backs)
                    out_edges[me].add(target)
                    in_edges[target].add(me)
                    changed = True
                else:
                    all_handles = [x["Handle"] for x in personas if x["Handle"] != me]
                    random_target = random.choice(all_handles)
                    if random_target not in out_edges[me]:
                        out_edges[me].add(random_target)
                        in_edges[random_target].add(me)
                        changed = True

            # Need >= 2 followers
            if F_count < 2:
                potential_followers = [
                    x for x in out_edges[me]
                    if me not in out_edges[x]
                ]
                if potential_followers:
                    target = random.choice(potential_followers)
                    out_edges[target].add(me)
                    in_edges[me].add(target)
                    changed = True
                else:
                    all_handles = [x["Handle"] for x in personas if x["Handle"] != me]
                    random_user = random.choice(all_handles)
                    if me not in out_edges[random_user]:
                        out_edges[random_user].add(me)
                        in_edges[me].add(random_user)
                        changed = True

        if not changed:
            break

    final_edges = []
    for u in out_edges:
        for v in out_edges[u]:
            final_edges.append((u, v))
    return final_edges


#############################
# 4. NETWORK DIAGRAM        #
#############################

def display_network_graph(edges, handle_to_name):
    """
    Display the network using PyVis inside Streamlit.
    Node size is scaled by number of incoming edges (in-degree).
    Label each node by its Name, not its Handle.
    """
    G = nx.DiGraph()
    for (follower, followed) in edges:
        G.add_node(follower)
        G.add_node(followed)
        G.add_edge(follower, followed)

    net = Network(height="600px", width="100%", directed=True, bgcolor="#222222", font_color="white")
    net.toggle_physics(True)

    in_degs = dict(G.in_degree())

    for node in G.nodes():
        in_degree_val = in_degs[node]
        base_size = 10
        scale_factor = 3
        node_size = base_size + scale_factor * in_degree_val

        name_label = handle_to_name.get(node, node)
        tooltip_text = f"{name_label}\nFollowers (in-degree): {in_degree_val}"

        net.add_node(
            node, 
            label=name_label,     
            size=node_size,
            title=tooltip_text
        )

    for edge in G.edges():
        net.add_edge(edge[0], edge[1])

    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_file:
        net.save_graph(tmp_file.name)
        tmp_file.seek(0)
        html_data = tmp_file.read().decode("utf-8")

    st.components.v1.html(html_data, height=600, scrolling=True)


#############################
# 5. RUN THE APP            #
#############################

if __name__ == "__main__":
    main()
