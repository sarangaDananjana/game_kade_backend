"""Readable two-word location names, separate from delivery PINs."""

from secrets import choice


ADJECTIVES = tuple('Amber Apricot Aqua Azure Beige Berry Blue Brass Bronze Brown Burgundy Carmine Cedar Cerise Cherry Chestnut Cobalt Copper Coral Cream Crimson Cyan Emerald Fawn Fern Gold Golden Green Hazel Indigo Ivory Jade Lavender Lemon Lilac Lime Magenta Mahogany Maroon Mauve Mint Moss Navy Olive Orange Peach Pearl Pink Plum Purple Red Rose Ruby Rust Saffron Sage Salmon Scarlet Silver Slate Snow Tan Teal Turquoise Violet White Yellow Bright Calm Clear Cool Cozy Crisp Daring Dawn Dreamy Early Fair Fancy Gentle Glad Grand Happy Honest Jolly Kind Light Little Lively Lucky Merry Mild Modest Neat Noble Playful Quiet Quick Royal Shiny Silky Smooth Soft Solar Spring Starry Still Sunny Sweet Tender Tidy Warm Wild Windy Wise Witty Young Bold Brisk Clever Coastal Dapper Eager Fresh Frosty Humble Icy Keen Loyal Mellow Nimble Patient Peaceful Proud Rare Ready Round Serene Sharp Simple Solid Swift Tall Tiny True Vivid Wavy Winter Wonder'.split())
NOUNS = tuple('Acacia Albatross Almond Alpaca Apple Apricot Aster Avocado Azalea Badger Bamboo Banyan Basil Bay Bear Beaver Beech Berry Birch Blossom Bluebell Boat Brook Bud Butterfly Cactus Camellia Canary Canoe Carrot Cashew Cedar Cherry Chestnut Clover Coconut Comet Coral Crane Creek Cricket Crocus Cypress Daisy Deer Dolphin Dove Dragonfly Duck Eagle Elm Falcon Feather Fern Finch Fir Firefly Flamingo Flower Forest Fox Freesia Frog Garden Gazelle Gecko Ginger Giraffe Goose Grove Guava Gull Harbor Hare Hazel Hedgehog Heron Hibiscus Hill Holly Honey Hummingbird Iris Island Ivy Jackfruit Jasmine Jay Juniper Kangaroo Kite Kiwi Koala Lake Lark Laurel Leaf Lemon Leopard Lily Lime Linden Lion Lotus Lynx Magnolia Mango Maple Marigold Meadow Melon Mimosa Moon Moose Moss Mountain Myrtle Nectar Oak Ocean Olive Orchid Otter Owl Palm Panda Papaya Parrot Peach Peacock Pear Pebble Pelican Penguin Pepper Petal Pine Plum Pond Poppy Puffin Pumpkin Quail Rabbit Rainbow Reed Reef Robin Rock Rosemary Rowan Sail Sand Seagull Shell Shore Sparrow Spice Spruce Star Stork Stream Swan Tamarind Tea Thistle Thyme Tiger Toucan Tree Tulip Turtle Valley Violet Walnut Waterfall Wave Willow Wind Wren Zebra Zinnia Anchor Basket Beacon Bell Blossom Breeze Bridge Canyon Castle Cloud Compass Crystal Diamond Drum Dune Echo Flute Fountain Galaxy Gate Gem Lantern Marble Melody Music Nest Oasis Opal Pebble Piano Planet Prism Ribbon River Sailboat Sandal Seed Song Spark Stone Sun Topaz Trail Umbrella Voyage Water Wheel Wing Wood'.split())


def generate_location_identity():
    return f'{choice(ADJECTIVES)} {choice(NOUNS)}'


# Historical migration 0007 imports this name. Runtime orders have no identity field.
generate_order_identity = generate_location_identity
