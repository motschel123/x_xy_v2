from xml.etree import ElementTree

import jax.numpy as jnp

import x_xy
from x_xy import base


def _find_assert_unique(tree: ElementTree, *keys):
    assert len(keys) > 0

    value = tree.findall(keys[0])
    if len(value) == 0:
        return None

    assert len(value) == 1

    if len(keys) == 1:
        return value[0]
    else:
        return _find_assert_unique(value[0], *keys[1:])


def _build_defaults_attributes(tree):
    tags = ["geom", "body"]
    default_attrs = {}
    for tag in tags:
        defaults_subtree = _find_assert_unique(tree, "defaults", tag)
        if defaults_subtree is None:
            attrs = {}
        else:
            attrs = defaults_subtree.attrib
        default_attrs[tag] = attrs
    return default_attrs


def _assert_all_tags_attrs_valid(xml_tree):
    valid_attrs = {
        "x_xy": ["model"],
        "options": ["gravity", "dt"],
        "defaults": ["geom", "body"],
        "worldbody": [],
        "body": ["name", "pos", "quat", "euler", "joint", "armature", "damping"],
        "geom": ["type", "mass", "pos", "dim"],
    }
    for subtree in xml_tree.iter():
        assert subtree.tag in list([key for key in valid_attrs])
        for attr in subtree.attrib:
            if subtree.tag == "geom" and attr.split("_")[0] == "vispy":
                continue
            assert attr in valid_attrs[subtree.tag]


def _mix_in_defaults(worldbody, default_attrs):
    for subtree in worldbody.iter():
        if subtree.tag not in ["body", "geom"]:
            continue
        tag = subtree.tag
        attr = subtree.attrib
        for default_attr in default_attrs[tag]:
            if default_attr not in attr:
                attr.update({default_attr: default_attrs[tag][default_attr]})


def _vispy_subdict(attr: dict):
    def delete_prefix(key):
        len_suffix = len(key.split("_")[0]) + 1
        return key[len_suffix:]

    return {delete_prefix(k): attr[k] for k in attr if k.split("_")[0] == "vispy"}


def _convert_attrs_to_arrays(xml_tree):
    for subtree in xml_tree.iter():
        for k, v in subtree.attrib.items():
            try:
                array = [float(num) for num in v.split(" ")]
            except:
                continue
            subtree.attrib[k] = jnp.squeeze(jnp.array(array))


def load_sys_from_str(xml_str: str):
    xml_tree = ElementTree.fromstring(xml_str)
    options = _find_assert_unique(xml_tree, "options").attrib
    default_attrs = _build_defaults_attributes(xml_tree)
    worldbody = _find_assert_unique(xml_tree, "worldbody")

    _assert_all_tags_attrs_valid(xml_tree)
    _convert_attrs_to_arrays(xml_tree)
    _mix_in_defaults(worldbody, default_attrs)

    links = {}
    link_parents = {}
    link_names = {}
    link_types = {}
    geoms = {}
    armatures = {}
    dampings = {}
    global_link_idx = -1

    def process_body(body: ElementTree, parent: int):
        nonlocal global_link_idx
        global_link_idx += 1
        current_link_idx = global_link_idx

        link_parents[current_link_idx] = parent
        link_types[current_link_idx] = body.attrib["joint"]
        link_names[current_link_idx] = body.attrib["name"]

        pos = body.attrib.get("pos", jnp.array([0.0, 0, 0]))
        rot = body.attrib.get("quat", None)
        if rot is not None:
            assert "euler" not in body.attrib
        elif "euler" in body.attrib:
            rot = base.maths.quat_euler(jnp.deg2rad(body.attrib["euler"]))
        else:
            rot = jnp.array([1.0, 0, 0, 0])
        links[current_link_idx] = base.Link(base.Transform(pos, rot))

        qd_size = base.QD_WIDTHS[body.attrib["joint"]]
        damping = body.attrib.get("damping", jnp.zeros((qd_size,)))
        armature = body.attrib.get("armature", jnp.zeros((qd_size,)))
        armatures[current_link_idx] = jnp.atleast_1d(armature)
        dampings[current_link_idx] = jnp.atleast_1d(damping)

        geom_map = {
            "box": lambda m, pos, dim, vispy: base.Box(m, pos, *dim, vispy),
            "sphere": lambda m, pos, dim, vispy: base.Sphere(m, pos, dim[0], vispy),
            "cylinder": lambda m, pos, dim, vispy: base.Cylinder(
                m, pos, dim[0], dim[1], vispy
            ),
        }
        link_geoms = []
        for geom_subtree in body.findall("geom"):
            g_attr = geom_subtree.attrib
            geom = geom_map[g_attr["type"]](
                g_attr["mass"], g_attr["pos"], g_attr["dim"], _vispy_subdict(g_attr)
            )
            link_geoms.append(geom)
        geoms[current_link_idx] = link_geoms

        for subbodies in body.findall("body"):
            process_body(subbodies, current_link_idx)

        return

    for body in worldbody.findall("body"):
        process_body(body, -1)

    def assert_order_then_to_list(d: dict) -> list:
        assert [i for i in d] == list(range(len(d)))
        return [d[i] for i in d]

    links = assert_order_then_to_list(links)
    links = links[0].batch(*links[1:])
    dampings = jnp.concatenate(assert_order_then_to_list(dampings))
    armatures = jnp.concatenate(assert_order_then_to_list(armatures))

    sys = base.System(
        assert_order_then_to_list(link_parents),
        links,
        assert_order_then_to_list(link_types),
        dampings,
        armatures,
        options["dt"],
        False,
        assert_order_then_to_list(geoms),
        options["gravity"],
        link_names=assert_order_then_to_list(link_names),
    )

    return x_xy.io.parse_system(sys)


def load_sys_from_xml(xml_path: str):
    with open(xml_path, "r") as f:
        xml_str = f.read()
    return load_sys_from_str(xml_str)