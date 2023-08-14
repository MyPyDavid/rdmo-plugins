import json
import tempfile
from collections import defaultdict
from os import makedirs
from os.path import isfile
from os.path import join as pj
from os.path import realpath
from pathlib import Path

import toml
from django import forms
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from rdmo.core.utils import import_class
from rdmo.projects.exports import Export
from rdmo.services.providers import OauthProviderMixin
from rocrate.rocrate import ROCrate


class ROCrateExport(OauthProviderMixin, Export):
    class Form(forms.Form):
        dataset = forms.CharField(label=_("Select dataset of your project"))

        def __init__(self, *args, **kwargs):
            dataset_choices = kwargs.pop("dataset_choices")
            super().__init__(*args, **kwargs)

            self.fields["dataset"].widget = forms.CheckboxSelectMultiple(
                choices=dataset_choices, attrs={"checked": "checked"}
            )

        def clean_dataset(self):
            data = self.data.getlist("dataset")
            data = list(map(int, data))
            return data

    def load_config(self, file_name):
        scriptname = realpath(__file__)
        scriptdir = "/".join(scriptname.split("/")[:-1])
        file_name_full = pj(scriptdir, file_name)
        if isfile(file_name_full) is False:
            print("toml file does not exist: " + file_name_full)
        else:
            with open(file_name_full) as filedata:
                try:
                    data = filedata.read()
                    d = toml.loads(data)
                    return d
                except Exception as e:
                    print("toml decode error: " + str(file_name_full))
                    raise (e)
        return None

    def render(self):
        datasets = self.get_set("project/dataset/id")
        dataset_choices = [(dataset.set_index, dataset.value) for dataset in datasets]

        self.store_in_session(self.request, "dataset_choices", dataset_choices)

        form = self.Form(dataset_choices=dataset_choices)

        return render(
            self.request, "plugins/exports_rocrate.html", {"form": form}, status=200
        )

    def submit(self):
        dataset_choices = self.get_from_session(self.request, "dataset_choices")
        form = self.Form(self.request.POST, dataset_choices=dataset_choices)

        if "cancel" in self.request.POST:
            return redirect("project", self.project.id)

        if form.is_valid():
            config = self.load_config("default.toml")
            dataset_selection = form.cleaned_data["dataset"]
            temp_folder = self.get_rocrate(config, dataset_selection)
            with open(pj(temp_folder, "ro-crate-metadata.json")) as json_file:
                file_contents = json.loads(json_file.read())
            response = HttpResponse(
                json.dumps(file_contents, indent=2),
                content_type="application/json",
            )
            response["Content-Disposition"] = 'filename="%s.json"' % self.project.title
            return response
        else:
            return render(
                self.request, "plugins/exports_rocrate.html", {"form": form}, status=200
            )

    def get_rocrate(self, config, dataset_selection):
        crate = ROCrate()
        crate.name = self.project.title
        # crate.description = self.project.description
        temp_folder = pj(tempfile.gettempdir(), "rocrate")
        rocrate_results = {}
        rocrate_results['datasets'] = {}
        for set_index in dataset_selection:
            # get_rocrate_object_from_rdmo_project_dataset_id
            dataset = self.get_dataset_by_id(config['dataset'], set_index)
            rocrate_results['datasets'][set_index] = dataset
            # persons = self.get_person_by_dataset_id(config["rocrate.model.person.Person"], set_index)
        
        # self.iterate_root(
        #     temp_folder, crate, config, dataset_selection=dataset_selection
        # )
        # if "file_name" in node_properties:
        for key, value in rocrate_results["datasets"].items():
            file_name = value.pop("file_name")
            folder_path = pj(temp_folder, file_name)
            makedirs(folder_path, exist_ok=True)
            crate.add_dataset(folder_path, properties=value)
        

        # for dataset in self.get_datasets():
        #     dataset_properties = {"name": dataset["title"]}
        #     makedirs(pj(temp_folder, dataset["file_name"]), exist_ok=True)
        #     if dataset.get("description"):
        #         dataset_properties["description"] = dataset["description"]

        #     crate.add_dataset(
        #         pj(temp_folder, dataset["file_name"]), properties=dataset_properties
        #     )
        crate.write(temp_folder)
        crate.write_zip(temp_folder + ".zip")
        return temp_folder


    def get_text_from_item_list(self, values, dataset_id) -> str:
        for item in values:
            text = self.get_text(item, set_index=dataset_id)
            if text:
                return text


    def get_dataset_by_id(self, dataset_config, set_index) -> dict:
        result = {}
        dataset_config.update({key: [value] for key, value in dataset_config.items() if isinstance(value, str)})
        for key, value in dataset_config.items():
            text = self.get_text_from_item_list(value, set_index)
            if text is None:
                text = f'{key} #{str(set_index + 1)}'
            result[key] = text
        return result                

    
    def get_persons(self, person_config, set_index) -> dict:
        persons = {}
        rdmo_persons = self.get_values("project/dataset/creator/name", set_index=set_index)
        set_index = rdmo_persons.set_index
        # if set_index in dataset_selection:
        node_properties = self.iterate_node(
            crate, value, set_index=set_index
        )

        found = False
        for pers in persons.values():
            try:
                node_properties["name"]
                pers.properties()["name"]
            except:
                pass
            else:
                if (
                    node_properties["name"]
                    == pers.properties()["name"]
                ):
                    found = True
                    persons[set_index] = pers
                    break
        if found is False:
            persons[set_index] = crate.add(
                import_class(key)(crate, properties=node_properties)
            )
        return persons

    def iterate_root(self, crate_folder, crate, tree, dataset_selection=[]):
        datasets = {}
        persons = {}
        for key, value in tree.items():
            if isinstance(value, str):
                setattr(crate, key, ", ".join(self.get_list(value)))
            elif isinstance(value, list):
                for val in value:
                    db_val = self.get_list(val)
                    if db_val:
                        setattr(crate, key, ", ".join(db_val))
                        break
            elif isinstance(value, dict):
                if "dataset" in key:
                    for rdmo_dataset in self.get_set("project/dataset/id"):
                        set_index = rdmo_dataset.set_index
                        if set_index in dataset_selection:
                            node_properties = self.iterate_node(
                                crate, value, set_index=set_index
                            )

                            if "file_name" in node_properties:
                                file_name = node_properties.pop("file_name")
                                folder_path = pj(crate_folder, file_name)
                                makedirs(folder_path, exist_ok=True)

                            datasets[set_index] = getattr(crate, key)(
                                folder_path, properties=node_properties
                            )

                elif "person" in key:
                    # self.get_persons()
                    pass
                else:
                    self.iterate_node(crate_folder, crate, value, key)
            else:
                raise ValueError("Expected string or list as value for ro crate config")

        for set_index, dataset in datasets.items():
            if set_index in persons:
                dataset["author"] = persons[set_index]

    def iterate_node(self, crate, tree, set_index=None):
        node_properties = {}
        for key, value in tree.items():
            if isinstance(value, str):
                node_properties[key] = ", ".join(
                    self.get_list(value, set_index=set_index)
                )
            elif isinstance(value, list):
                for val in value:
                    db_val = self.get_list(val, set_index=set_index)
                    if db_val:
                        node_properties[key] = ", ".join(db_val)
                        break
            elif isinstance(value, dict):
                self.iterate_node(crate, value, key, set_index=set_index)
            else:
                raise ValueError("Expected string or list as value for ro crate config")
        return node_properties

        # scheme_uri = {
        #     'INSI': 'http://www.isni.org/',
        #     'ORCID': 'https://orcid.org',
        #     'ROR': 'https://ror.org/',
        #     'GRID': 'https://www.grid.ac/'
        # }

        # def render_document(self, xml, dataset):
        #     xml.startElement('resource', {
        #         'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        #         'xmlns': 'http://datacite.org/schema/kernel-4',
        #         'xsi:schemaLocation': 'http://datacite.org/schema/kernel-4 http://schema.datacite.org/meta/kernel-4.3/metadata.xsd'
        #     })

        #     # identifier
        #     identifier = dataset.get('identifier')
        #     if identifier:
        #         self.render_text_element(xml, 'identifier', {
        #             'identifierType': dataset.get('identifierType', 'OTHER')
        #         }, identifier)

        #     # creators
        #     creators = dataset.get('creators')
        #     if creators:
        #         xml.startElement('creators', {})
        #         for creator in creators:
        #             xml.startElement('creator', {})
        #             self.render_text_element(xml, 'creatorName', {
        #                 'nameType': creator.get('nameType')
        #             }, creator.get('name'))

        #             if creator.get('givenName'):
        #                 self.render_text_element(xml, 'givenName', {}, creator.get('givenName'))

        #             if creator.get('familyName'):
        #                 self.render_text_element(xml, 'familyName', {}, creator.get('familyName'))

        #             if creator.get('nameIdentifier'):
        #                 self.render_text_element(xml, 'nameIdentifier', {
        #                     'nameIdentifierScheme': creator.get('nameIdentifierScheme'),
        #                     'schemeURI': self.scheme_uri.get(creator.get('schemeURI')),
        #                 }, creator.get('nameIdentifier'))

        #             for affiliation in creator.get('affiliations', []):
        #                 self.render_text_element(xml, 'affiliation', {
        #                     'affiliationIdentifier': affiliation.get('affiliationIdentifier'),
        #                     'affiliationIdentifierScheme': affiliation.get('affiliationIdentifierScheme')
        #                 }, affiliation.get('affiliation'))

        #             xml.endElement('creator')
        #         xml.endElement('creators')

        #     # titles
        #     titles = dataset.get('titles')
        #     if titles:
        #         xml.startElement('titles', {})
        #         for title in titles:
        #             self.render_text_element(xml, 'title', {
        #                 'titleType': title.get('titleType')
        #             }, title.get('title'))
        #         xml.endElement('titles')

        #     # publisher
        #     publisher = dataset.get('publisher')
        #     if publisher:
        #         self.render_text_element(xml, 'publisher', {}, publisher)

        #     # publicationYear
        #     publication_year = dataset.get('publicationYear')
        #     if publication_year:
        #         self.render_text_element(xml, 'publicationYear', {}, publication_year)

        #     # subjects
        #     subjects = dataset.get('subjects')
        #     if subjects:
        #         xml.startElement('subjects', {})
        #         for subject in subjects:
        #             self.render_text_element(xml, 'subject', {
        #                 'subjectScheme': subject.get('subjectScheme'),
        #                 'schemeURI': subject.get('schemeURI')
        #             }, subject.get('subject'))
        #         xml.endElement('subjects')

        #     # contributors
        #     contributors = dataset.get('contributors')
        #     if contributors:
        #         xml.startElement('contributors', {})
        #         for contributor in dataset.get('contributors', []):
        #             xml.startElement('contributor', {
        #                 'contributorType': contributor.get('contributorType')
        #             })
        #             self.render_text_element(xml, 'contributorName', {
        #                 'nameType': contributor.get('nameType')
        #             }, contributor.get('name'))

        #             if contributor.get('givenName'):
        #                 self.render_text_element(xml, 'givenName', {}, contributor.get('givenName'))

        #             if contributor.get('familyName'):
        #                 self.render_text_element(xml, 'familyName', {}, contributor.get('familyName'))

        #             if contributor.get('nameIdentifier'):
        #                 self.render_text_element(xml, 'nameIdentifier', {
        #                     'nameIdentifierScheme': contributor.get('nameIdentifierScheme'),
        #                     'schemeURI': self.scheme_uri.get(contributor.get('schemeURI')),
        #                 }, contributor.get('nameIdentifier'))

        #             for affiliation in contributor.get('affiliations', []):
        #                 self.render_text_element(xml, 'affiliation', {
        #                     'affiliationIdentifier': affiliation.get('affiliationIdentifier'),
        #                     'affiliationIdentifierScheme': affiliation.get('affiliationIdentifierScheme')
        #                 }, affiliation.get('affiliation'))

        #             xml.endElement('contributor')
        #         xml.endElement('contributors')

        #     # dates
        #     created = dataset.get('created')
        #     issued = dataset.get('issued')
        #     if created or issued:
        #         xml.startElement('dates', {})
        #         if created:
        #             self.render_text_element(xml, 'date', {
        #                 'dateType': 'Created'
        #             }, created)
        #         if issued:
        #             self.render_text_element(xml, 'date', {
        #                 'dateType': 'Issued'
        #             }, issued)
        #         xml.endElement('dates')

        #     # language
        #     language = dataset.get('language')
        #     if language:
        #         self.render_text_element(xml, 'language', {}, language)

        #     # resource_type
        #     resource_type = dataset.get('resourceType')
        #     if resource_type:
        #         self.render_text_element(xml, 'resourceType', {
        #             'resourceTypeGeneral': dataset.get('resourceTypeGeneral')
        #         }, resource_type)

        #     # alternateIdentifiers
        #     alternate_identifiers = dataset.get('alternateIdentifiers')
        #     if alternate_identifiers:
        #         xml.startElement('alternateIdentifiers', {})
        #         for alternate_identifier in alternate_identifiers:
        #             self.render_text_element(xml, 'alternateIdentifier', {
        #                 'alternateIdentifierType': alternate_identifier.get('alternateIdentifierType')
        #             }, alternate_identifier.get('alternateIdentifier'))
        #         xml.endElement('alternateIdentifiers')

        #     # related_identifiers
        #     related_identifiers = dataset.get('relatedIdentifiers')
        #     if related_identifiers:
        #         xml.startElement('relatedIdentifiers', {})
        #         for related_identifier in related_identifiers:
        #             self.render_text_element(xml, 'relatedIdentifier', {
        #                 'relatedIdentifierType': related_identifier.get('relatedIdentifierType'),
        #                 'relationType': related_identifier.get('relationType')
        #             }, related_identifier.get('relatedIdentifier'))
        #         xml.endElement('relatedIdentifiers')

        #     # rights list
        #     rights_list = dataset.get('rightsList')
        #     if rights_list:
        #         xml.startElement('rightsList', {})
        #         for rights in rights_list:
        #             self.render_text_element(xml, 'rights', {
        #                 'rightsURI': rights.get('rightsURI')
        #             }, rights.get('rights'))
        #         xml.endElement('rightsList')

        #     # descriptions
        #     descriptions = dataset.get('descriptions')
        #     if descriptions:
        #         xml.startElement('descriptions', {})
        #         for description in descriptions:
        #             self.render_text_element(xml, 'description', {
        #                 'descriptionType': description.get('descriptionType', 'Abstract')
        #             }, description.get('description'))
        #         xml.endElement('descriptions')

        #     # funding_references
        #     funding_references = dataset.get('fundingReferences')
        #     if funding_references:
        #         xml.startElement('fundingReferences', {})
        #         for funding_reference in funding_references:
        #             xml.startElement('fundingReference', {})
        #             self.render_text_element(xml, 'funderName', {}, funding_reference.get('funderName'))
        #             self.render_text_element(xml, 'funderIdentifier', {
        #                 'schemeURI': self.scheme_uri.get(funding_reference.get('funderIdentifierType')),
        #                 'funderIdentifierType': funding_reference.get('funderIdentifierType')
        #             }, funding_reference.get('funderIdentifier'))
        #             if funding_reference.get('awardNumber'):
        #                 self.render_text_element(xml, 'awardNumber', {
        #                     'awardURI': funding_reference.get('awardURI')
        #                 }, funding_reference.get('awardNumber'))
        #             if funding_reference.get('awardTitle'):
        #                 self.render_text_element(xml, 'awardTitle', {}, funding_reference.get('awardTitle'))
        #             xml.endElement('fundingReference')
        #         xml.endElement('fundingReferences')

        #     xml.endElement('resource')

    def get_datasets(self):
        datasets = []
        for rdmo_dataset in self.get_set("project/dataset/id"):
            set_index = rdmo_dataset.set_index
            dataset = defaultdict(list)

            # file_name
            dataset["file_name"] = "{}".format(
                self.get_text("project/dataset/identifier", set_index=set_index)
                or self.get_text("project/dataset/id", set_index=set_index)
                or str(set_index + 1)
            )
            dataset["title"] = (
                self.get_text("project/dataset/title", set_index=set_index)
                or self.get_text("project/dataset/id", set_index=set_index)
                or "Dataset #{}".format(set_index + 1)
            )

            # identifier
            # identifier = self.get_text('project/dataset/identifier', set_index=set_index)
            # if identifier:
            #     dataset['identifier'] = identifier
            #     dataset['identifierType'] = \
            #         self.get_option(self.identifier_type_options, 'project/dataset/identifier_type', set_index=set_index) or \
            #         self.get_option(self.identifier_type_options, 'project/dataset/pids/system', set_index=set_index) or \
            #         'OTHER'
            # else:
            #     dataset['identifier'] = self.get_text('project/dataset/id', set_index=set_index)
            #     dataset['identifierType'] = 'OTHER'

            # creators
            # for creator_set in self.get_set('project/dataset/creator/name', set_prefix=str(set_index)):
            #     creator = self.get_name('project/dataset/creator',
            #                             set_prefix=creator_set.set_prefix, set_index=creator_set.set_index)
            #     if creator:
            #         dataset['creators'].append(creator)

            # titles


            # publisher
            # publisher = \
            #     self.get_text('project/dataset/publisher', set_index=set_index) or \
            #     self.get_text('project/dataset/preservation/repository', set_index=set_index)
            # if publisher:
            #     dataset['publisher'] = publisher

            # publication_year
            # dataset['publicationYear'] = self.get_year('project/dataset/data_publication_date', set_index=set_index)

            # subjects
            # subjects = \
            #     self.get_values('project/dataset/subject', set_index=set_index) or \
            #     self.get_values('project/research_field/title', set_index=set_index)
            # if subjects:
            #     dataset['subjects'] = [{
            #         'subject': subject.value
            #     } for subject in subjects]

            # contributors
            # for contributor_set in self.get_set('project/dataset/contributor/name', set_prefix=str(set_index)):
            #     contributor = self.get_name('project/dataset/contributor',
            #                                 set_prefix=contributor_set.set_prefix, set_index=contributor_set.set_index)
            #     if contributor:
            #         dataset['contributors'].append(contributor)

            # dates
            # dataset['created'] =  \
            #     self.get_timestamp('project/dataset/date/created', set_index=set_index)
            # dataset['issued'] =  \
            #     self.get_timestamp('project/dataset/date/issued', set_index=set_index) or \
            #     self.get_timestamp('project/dataset/data_publication_date', set_index=set_index)

            # language
            # dataset['language'] = self.get_option(self.language_options, 'project/dataset/language', set_index=set_index)

            # resource_type
            # resource_type = self.get_text('project/dataset/resource_type', set_index=set_index)
            # if resource_type:
            #     dataset['resourceType'] = resource_type
            #     dataset['resourceTypeGeneral'] = \
            #         self.get_option(self.resource_type_general_options, 'project/dataset/resource_type_general', set_index=set_index)

            # alternate_identifiers
            # for alternate_identifier_set in self.get_set('project/dataset/alternate_identifier/identifier', set_prefix=str(set_index)):
            #     dataset['alternateIdentifiers'].append({
            #         'alternateIdentifier': self.get_text('project/dataset/alternate_identifier/identifier',
            #                                              set_prefix=alternate_identifier_set.set_prefix,
            #                                              set_index=alternate_identifier_set.set_index),
            #         'alternateIdentifierType': self.get_option(self.identifier_type_options,
            #                                                    'project/dataset/alternate_identifier/identifier_type',
            #                                                    set_prefix=alternate_identifier_set.set_prefix,
            #                                                    set_index=alternate_identifier_set.set_index)
            #     })

            # related_identifiers
            # for related_identifier_set in self.get_set('project/dataset/related_identifier/identifier', set_prefix=str(set_index)):
            #     dataset['relatedIdentifiers'].append({
            #         'relatedIdentifier': self.get_text('project/dataset/related_identifier/identifier',
            #                                            set_prefix=related_identifier_set.set_prefix,
            #                                            set_index=related_identifier_set.set_index),
            #         'relatedIdentifierType': self.get_option(self.identifier_type_options,
            #                                                  'project/dataset/related_identifier/identifier_type',
            #                                                  set_prefix=related_identifier_set.set_prefix,
            #                                                  set_index=related_identifier_set.set_index),
            #         'relationType': self.get_option(self.relation_type_options,
            #                                         'project/dataset/related_identifier/relation_type',
            #                                         set_prefix=related_identifier_set.set_prefix,
            #                                         set_index=related_identifier_set.set_index)
            #     })

            # rights
            # for rights in self.get_values('project/dataset/sharing/conditions', set_index=set_index):
            #     if rights.option:
            #         dataset['rightsList'].append({
            #             'rights': rights.value,
            #             'rightsURI': self.rights_uri_options.get(rights.option.path)
            #         })

            # description
            description = self.get_text(
                "project/dataset/description", set_index=set_index
            )
            if description:
                dataset["description"] = description

            # funding_references
            # for funder in self.get_set('project/funder/id'):
            #     dataset['fundingReferences'].append({
            #         'funderName': self.get_text('project/funder/name', set_index=funder.set_index),
            #         'funderIdentifier': self.get_text('project/funder/name_identifier', set_index=funder.set_index),
            #         'funderIdentifierType': self.get_option(self.name_identifier_scheme_options, 'project/funder/name_identifier_scheme', set_index=funder.set_index),
            #         'awardURI': self.get_text('project/funder/programme/url', set_index=funder.set_index),
            #         'awardNumber': self.get_text('project/funder/programme/number', set_index=funder.set_index),
            #         'awardTitle': self.get_text('project/funder/programme/title', set_index=funder.set_index)
            #     })

            datasets.append(dataset)

        return datasets

    def get_name(self, attribute, set_prefix="", set_index=0):
        name_text = self.get_text(
            attribute + "/name", set_prefix=set_prefix, set_index=set_index
        )
        if name_text:
            name = {
                "name": name_text,
                "nameType": self.get_option(
                    self.name_type_options,
                    attribute + "/name_type",
                    set_prefix=set_prefix,
                    set_index=set_index,
                    default="Personal",
                ),
            }

            # contributor_name
            contributor_type = self.get_option(
                self.contributor_type_options,
                attribute + "/contributor_type",
                set_prefix=set_prefix,
                set_index=set_index,
                default="Other",
            )
            if contributor_type:
                name["contributorType"] = contributor_type

            # given_name
            given_name = self.get_text(
                attribute + "/given_name", set_prefix=set_prefix, set_index=set_index
            )
            if given_name:
                name["givenName"] = given_name

            # family_name
            family_name = self.get_text(
                attribute + "/family_name", set_prefix=set_prefix, set_index=set_index
            )
            if family_name:
                name["familyName"] = family_name

            # identifier
            identifier = self.get_text(
                attribute + "/name_identifier",
                set_prefix=set_prefix,
                set_index=set_index,
            )
            if identifier:
                name["nameIdentifier"] = identifier
                name["nameIdentifierScheme"] = self.get_option(
                    self.name_identifier_scheme_options,
                    attribute + "/name_identifier_scheme",
                    set_prefix=set_prefix,
                    set_index=set_index,
                    default="ORCID",
                )

            # affiliations
            affiliations = self.get_list(
                attribute + "/affiliation", set_prefix=set_prefix, set_index=set_index
            )
            if affiliations:
                name["affiliations"] = []
                for affiliation in affiliations:
                    name["affiliations"].append({"affiliation": affiliation})

            return name
        else:
            return None
